"""
LinkedIn queue management and PhantomBuster result parsing.

This module provides the shared helpers that both the orchestration handlers
and the linkedin_worker use. It is NOT the execution engine — that moved to
linkedin_worker.py (Phase 2 / P2-3).

KEY FUNCTIONS:
  - queue_linkedin_action_no_commit(): adds a row to linkedin_queue WITHOUT
    committing. Used by orchestration handlers (who share the scheduler's
    transaction). This is the P2-4 / CRITICAL-2 fix for split-brain.
  - queue_linkedin_action(): same but with commit. For admin endpoints, CLI,
    and standalone callers that own their own session.
  - classify_phantom_row(): classifies a single PB result row as success/
    neutral/pending/failed/unknown using the PHANTOM_RESULT_SCHEMA from config.
  - normalize_linkedin_url(): canonicalises LinkedIn URLs for matching
    (strips scheme, www, trailing slash).
  - _process_results(): reconciles PB container results with queue items.
  - _pause_orch_state_for_linkedin_failure(): handles failed LinkedIn actions
    by logging the failure and letting the sequence continue (LinkedIn failures
    don't block other channels).

DEPRECATED:
  - flush_linkedin_queue(): shim that calls into the worker. Remove in Phase 3.
  - _is_success_status(): legacy substring check. Use classify_phantom_row() instead.
  - _parse_console_output(): no-op stub. PB console output parsing was fragile.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pause_orch_state_for_linkedin_failure(
    db: Session,
    prospect_id: str,
    action_type: str,
    verdict: str,
    detail: Optional[str],
) -> None:
    """
    Handle a LinkedIn failure by letting the sequence continue.

    IMPORTANT DESIGN DECISION: despite the name, this function does NOT
    actually pause the sequence (as of 2026-05-25). A failed LinkedIn
    connect/message should NOT block the entire sequence because email
    and LinkedIn are independent channels. This mirrors the bounce
    handling: a bounced email suppresses the email channel but LinkedIn
    keeps firing.

    What it actually does:
      1. Finds the active orch state for the prospect
      2. If the state was previously paused by a LinkedIn failure, UNPAUSES it
      3. Increments consecutive_failure_count for monitoring
      4. Logs the failure at WARNING level

    The orch handler already advanced current_step_index when it queued
    the LinkedIn action, so the state is already past the failed step.
    No rewind, no pause — just logging and counter increment.

    The function name is historical — it used to pause, but the behaviour
    was changed. Renaming would break import paths across the codebase.
    """
    from ..orchestration.orm import OrchSequenceStateORM

    try:
        row = (
            db.query(OrchSequenceStateORM)
            .filter(OrchSequenceStateORM.lead_id == prospect_id)
            .filter(OrchSequenceStateORM.status.in_(["active", "waiting_for_next_step", "paused"]))
            .order_by(OrchSequenceStateORM.updated_at.desc())
            .first()
        )
        if row is None:
            logger.info(
                "_pause_orch_state_for_linkedin_failure: no active orch "
                "state for lead=%s  -  skipping.",
                prospect_id,
            )
            return

        # If previously paused by an earlier LinkedIn failure, unpause
        # and let the sequence continue from where it is.
        if row.status == "paused" and row.paused_reason and "linkedin_" in row.paused_reason:
            row.status = "waiting_for_next_step"
            row.paused_reason = None
            row.scheduled_next_at = _utcnow()

        row.consecutive_failure_count = (row.consecutive_failure_count or 0) + 1
        row.updated_at = _utcnow()

        logger.warning(
            "LinkedIn %s %s for lead=%s (state %s): skipped, sequence continues. "
            "Detail: %s (failure_count=%d)",
            action_type, verdict, prospect_id, row.state_id,
            (detail or "")[:120], row.consecutive_failure_count,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "_pause_orch_state_for_linkedin_failure failed for lead=%s: %s",
            prospect_id, exc, exc_info=True,
        )


def flush_linkedin_queue(db: Session) -> dict:
    """DEPRECATED  -  use `linkedin_worker._drain_once()` instead.

    Kept as a shim so any third-party import path still works during the
    transition window. Forwards to the new worker, which does per-user
    grouping and a cancellable poll. Remove in Phase 3.
    """
    from .linkedin_worker import _drain_once
    _drain_once(db)
    return {"status": "ok", "message": "Drained via linkedin_worker"}


def classify_phantom_row(row: dict, action_type: str) -> tuple[str, str]:
    """
    Classify a single PhantomBuster result row using a schema-driven approach.

    PB returns different field names and status strings depending on the phantom
    type (Auto Connect vs Message Sender) and even the phantom version. Rather
    than hardcoding string comparisons, this function uses PHANTOM_RESULT_SCHEMA
    (defined in src/config.py) which maps each action_type to:
      - success_flags: boolean fields that indicate success (e.g., "sent")
      - neutral_flags: boolean fields for already-done states ("alreadyConnected")
      - accept_status: status strings that mean success ("invitation sent")
      - neutral_status: status strings for already-done ("already connected")
      - pending_status: status strings for not-yet-processed ("not invited yet")
      - error_field: fields that carry error messages
      - presence_success_fields: fields whose mere presence indicates success
      - url_field: fields containing the LinkedIn profile URL

    Returns a (verdict, detail) tuple where verdict is one of:
      "success"  → action confirmed successful
      "neutral"  → already-connected/messaged; treated as done
      "pending"  → PB knows the lead but hasn't processed it yet
      "failed"   → explicit error in the result row
      "unknown"  → unrecognisable result; caller must NOT mark as done

    The classification order matters: success flags → neutral flags → status
    string → error fields → presence check → unknown.
    """
    from ..config import PHANTOM_RESULT_SCHEMA

    schema = PHANTOM_RESULT_SCHEMA.get(action_type)
    if schema is None:
        return "unknown", f"no schema mapping for action_type={action_type}"

    if not isinstance(row, dict):
        return "unknown", "row is not a dict"

    # 1. Explicit success boolean flags.
    for flag in schema["success_flags"]:
        val = row.get(flag)
        if isinstance(val, bool) and val:
            return "success", flag
        # Strings sometimes encode "true"/"yes" for success flags.
        if isinstance(val, str) and val.strip().lower() in {"true", "yes", "1"}:
            return "success", flag

    # 2. Explicit neutral flags (already connected/messaged).
    for flag in schema["neutral_flags"]:
        val = row.get(flag)
        if isinstance(val, bool) and val:
            return "neutral", flag
        if isinstance(val, str) and val.strip().lower() in {"true", "yes", "1"}:
            return "neutral", flag

    # 3. Status string lookups against accept/neutral/pending sets.
    raw_status = row.get("status") or row.get("result")
    if isinstance(raw_status, str):
        norm = raw_status.strip().lower()
        if norm in schema["accept_status"]:
            return "success", norm
        if norm in schema["neutral_status"]:
            return "neutral", norm
        # Pending = PB knows the lead but hasn't processed it yet.
        # Return "pending" so caller can leave the item in processing state.
        pending_set = schema.get("pending_status", frozenset())
        if norm in pending_set:
            return "pending", norm
        # Non-empty unknown status  -  refuse to optimistically mark success.
        if norm:
            if "error" in norm or "fail" in norm or "denied" in norm:
                return "failed", norm
            return "unknown", norm

    # 4. Error fields.
    for field in schema["error_field"]:
        err = row.get(field)
        if isinstance(err, str) and err.strip():
            return "failed", err.strip()

    # 5. Presence-based success check (PB Message Sender fallback).
    #    Some PB phantoms don't set explicit success flags or status
    #    fields. Instead, the presence of certain fields (timestamp,
    #    senderName) in the result row indicates PB processed the lead
    #    and delivered the message. PB omits the row entirely for leads
    #    it skipped, so presence = delivery confirmation.
    presence_fields = schema.get("presence_success_fields", ())
    if presence_fields:
        present = [f for f in presence_fields if row.get(f)]
        if len(present) == len(presence_fields):
            return "success", f"presence:{','.join(present)}"

    # 6. Nothing recognised.
    return "unknown", "no schema fields present in row"


def normalize_linkedin_url(url: str) -> str:
    """
    Canonicalise a LinkedIn profile URL for matching across our queue
    rows and Phantom result rows.

    The Outbound Agent LinkedIn Outreach Phantom (2026-04-10) returns
    the same profile under two different fields:

      profileUrl         = 'https://www.linkedin.com/in/foo/'
      linkedinProfileUrl = 'http://www.linkedin.com/in/foo'

    while our queue rows store whatever Apollo / the operator entered,
    which may be either http:// or https:// and may or may not have a
    trailing slash. Comparing those raw forms misses the match and the
    row is wrongly classified ``unknown``, which (post-2026-04-10
    `_pause_orch_state_for_linkedin_failure`) pauses the orch state
    even though the connect actually went through.

    Strategy: lowercase, strip whitespace, drop the scheme, drop a
    leading ``www.``, drop the trailing slash. The result is a stable
    key both sides can use without ambiguity.
    """
    if not isinstance(url, str):
        return ""
    from urllib.parse import unquote
    s = unquote(url.strip()).lower()
    if s.startswith("https://"):
        s = s[len("https://"):]
    elif s.startswith("http://"):
        s = s[len("http://"):]
    if s.startswith("www."):
        s = s[len("www."):]
    return s.rstrip("/")


def _extract_url(row: dict, action_type: str) -> str:
    """Pick the first non-empty url-ish field per the schema, normalised."""
    from ..config import PHANTOM_RESULT_SCHEMA

    schema = PHANTOM_RESULT_SCHEMA.get(action_type)
    if schema is None:
        return ""
    for field in schema["url_field"]:
        val = row.get(field)
        if isinstance(val, str) and val.strip():
            return normalize_linkedin_url(val)
    return ""


def _process_results(
    client,
    container_id: str,
    agent_id: str,
    url_to_items: dict,
    action_type: str,
    db: Session,
) -> None:
    """
    Fetch the Phantom's structured result object and reconcile it with the
    queue items.

    P2-5 / deep audit HIGH-9: success detection now goes through
    `classify_phantom_row()` against `PHANTOM_RESULT_SCHEMA`. Optimistic
    "no per-profile status → mark done" is removed. If a row's verdict is
    `unknown` we leave the queue item in `processing` so the operator can
    investigate (or the next worker tick can retry).

    Also refuses to process action types that aren't in the schema dict.
    """
    from ..config import PHANTOM_RESULT_SCHEMA
    from ..db.models import LinkedInQueueStatus, Prospect

    if action_type not in PHANTOM_RESULT_SCHEMA:
        logger.error(
            "_process_results: refusing to process container %s  -  no schema "
            "for action_type=%s. Configure PHANTOM_RESULT_SCHEMA in src/config.py.",
            container_id, action_type,
        )
        return

    now = _utcnow()

    # Fetch structured result object  -  this is the only trusted source.
    try:
        result_data = client.get_container_result(container_id)
    except Exception as e:
        logger.warning("Could not fetch result object for %s: %s", container_id, e)
        result_data = None

    rows: list[dict] = []
    if isinstance(result_data, dict):
        result_obj = result_data.get("resultObject")
        # Some Phantoms return resultObject as a JSON-encoded string.
        if isinstance(result_obj, str):
            try:
                import json as _json
                result_obj = _json.loads(result_obj)
            except Exception:
                logger.warning(
                    "_process_results: container %s resultObject is a non-JSON string",
                    container_id,
                )
                result_obj = None
        if isinstance(result_obj, list):
            rows = [r for r in result_obj if isinstance(r, dict)]
        elif isinstance(result_obj, dict):
            inner = result_obj.get("results") or result_obj.get("data") or []
            if isinstance(inner, list):
                rows = [r for r in inner if isinstance(r, dict)]

    # Index rows by normalised url.
    row_by_url: dict[str, dict] = {}
    for row in rows:
        url = _extract_url(row, action_type)
        if url:
            row_by_url[url] = row

    unknown_count = 0
    success_count = 0
    neutral_count = 0
    failed_count = 0
    pending_count = 0

    for norm_url, queue_items in url_to_items.items():
        row = row_by_url.get(norm_url)
        if row is None:
            verdict, detail = "unknown", "no result row for url"
        else:
            verdict, detail = classify_phantom_row(row, action_type)

        for q in queue_items:
            q.processed_at = now
            q.result_detail = detail[:500] if detail else verdict

            if verdict == "success":
                q.status = LinkedInQueueStatus.done
                success_count += 1
                prospect = db.query(Prospect).filter_by(id=q.prospect_id).first()
                if prospect:
                    if action_type == "connect":
                        prospect.linkedin_connect_sent_at = now
                    elif action_type == "message":
                        prospect.linkedin_connected = True

            elif verdict == "neutral":
                q.status = LinkedInQueueStatus.done
                neutral_count += 1
                prospect = db.query(Prospect).filter_by(id=q.prospect_id).first()
                if prospect and action_type == "connect" and "already" in (detail or ""):
                    prospect.linkedin_connected = True

            elif verdict == "pending":
                # PB knows the lead but hasn't processed it yet. Keep in
                # processing so the async poller picks it up on the next pass.
                pending_count += 1
                # Don't change status - leave as processing

            elif verdict == "failed":
                q.status = LinkedInQueueStatus.failed
                failed_count += 1
                _pause_orch_state_for_linkedin_failure(
                    db, q.prospect_id, action_type, "failed", detail,
                )

            else:  # unknown
                unknown_count += 1
                q.status = LinkedInQueueStatus.failed
                _pause_orch_state_for_linkedin_failure(
                    db, q.prospect_id, action_type, "unknown", detail,
                )

    db.commit()
    logger.info(
        "LinkedIn batch results: container=%s action=%s success=%d neutral=%d failed=%d unknown=%d total=%d",
        container_id, action_type,
        success_count, neutral_count, failed_count, unknown_count,
        len(url_to_items),
    )


def _is_success_status(status: str, action_type: str) -> bool:
    """
    Legacy substring success check, kept for backwards-compat with any
    external import. New code should use `classify_phantom_row()` /
    `PHANTOM_RESULT_SCHEMA` instead. Deprecated, do not extend.
    """
    from ..config import PHANTOM_RESULT_SCHEMA

    if not status:
        return False
    schema = PHANTOM_RESULT_SCHEMA.get(action_type)
    if schema is None:
        return False
    norm = status.strip().lower()
    return norm in schema["accept_status"]


def _parse_console_output(output: str, url_to_items: dict) -> dict[str, str]:
    """
    DEPRECATED. Console output parsing is fragile against PhantomBuster UI
    wording changes. Kept as a no-op stub so any existing call site doesn't
    crash; the new `_process_results` ignores console output entirely and
    relies on the structured `containers/fetch-result-object` payload only.
    """
    return {}


def queue_linkedin_action(
    db: Session,
    prospect_id: str,
    campaign_id: Optional[str],
    action_type: str,
    linkedin_url: str,
    message_text: Optional[str] = None,
) -> str:
    """
    Add a LinkedIn action to the batch queue.

    NOTE: This commits on the supplied session. Orchestration handlers
    (which participate in the scheduler's transaction) MUST use
    `queue_linkedin_action_no_commit()` instead to avoid committing the
    queue row independently of the scheduler's commit boundary  -  see
    P2-4 / deep audit CRITICAL-2.

    Kept for admin endpoints, CLI tools, and standalone callers that own
    their own session.

    Returns the queue item ID.
    """
    queue_id = queue_linkedin_action_no_commit(
        db=db,
        prospect_id=prospect_id,
        campaign_id=campaign_id,
        action_type=action_type,
        linkedin_url=linkedin_url,
        message_text=message_text,
    )
    db.commit()
    return queue_id


def queue_linkedin_action_no_commit(
    db: Session,
    prospect_id: str,
    campaign_id: Optional[str],
    action_type: str,
    linkedin_url: str,
    message_text: Optional[str] = None,
    user_id: Optional[str] = None,
    linkedin_account_id: Optional[str] = None,
) -> str:
    """
    Add a LinkedIn action to the batch queue WITHOUT committing.

    P2-4 / deep audit CRITICAL-2 fix: orchestration handlers were opening
    a fresh `SessionLocal()` inside a transaction the scheduler already
    owned, and the old `queue_linkedin_action()` committed the queue row
    independently. If the scheduler later rolled back, the LinkedIn queue
    row was already live → split-brain (Phantom launched against a lead
    whose state change never made it to the DB).

    This variant `.add()` + `.flush()` the row on the caller's session and
    returns. The caller must commit  -  in practice that's the scheduler's
    `db.commit()` in `run_scheduler`. If that commit fails and rolls back,
    the queue row is rolled back with it, keeping orch state and the
    LinkedIn queue consistent.

    Returns the queue item ID.
    """
    from ..db.models import LinkedInQueue, LinkedInQueueStatus, Prospect

    # Suppression re-check: a prospect might have unsubscribed between
    # campaign launch (when the orch state was created) and now (when the
    # scheduler dispatches the step). Without this check, we'd queue a
    # LinkedIn connect/message for someone who opted out 5 minutes ago.
    prospect = db.query(Prospect).filter_by(id=prospect_id).first()
    if prospect and prospect.email:
        from ..api.unsubscribes import is_suppressed
        if is_suppressed(prospect.email, db):
            logger.info(
                "LinkedIn queue: skipping suppressed prospect %s (%s)",
                prospect_id, prospect.email,
            )
            return ""

    # Deduplication: prevent duplicate queue entries for the same prospect +
    # action + campaign. Without campaign_id in the filter, a prospect enrolled
    # in two campaigns would have their second queue entry silently merged into
    # the first, causing the second campaign's personalized message to be lost.
    existing = (
        db.query(LinkedInQueue)
        .filter_by(prospect_id=prospect_id, action_type=action_type, campaign_id=campaign_id)
        .filter(LinkedInQueue.status.in_([
            LinkedInQueueStatus.queued, LinkedInQueueStatus.processing,
        ]))
        .first()
    )
    if existing:
        logger.info(
            "LinkedIn queue: skipping duplicate %s for prospect %s campaign %s (existing=%s)",
            action_type, prospect_id, campaign_id, existing.id,
        )
        return existing.id

    item = LinkedInQueue(
        id=str(uuid.uuid4()),
        prospect_id=prospect_id,
        campaign_id=campaign_id,
        action_type=action_type,
        linkedin_url=linkedin_url,
        message_text=message_text,
        user_id=user_id,
        # Multi-account LinkedIn: route this action to a specific LinkedIn account's
        # PB phantom. NULL = legacy env-var resolution in the worker.
        linkedin_account_id=linkedin_account_id,
    )
    db.add(item)
    db.flush()
    logger.info(
        "LinkedIn queue (no-commit): added %s for prospect %s (%s)",
        action_type, prospect_id, linkedin_url,
    )
    return item.id
