"""
Event ingestion  -  normalises inbound webhook payloads into EngagementEvents
and drives state-machine transitions + scoring updates.

This is the inbound webhook handler for the orchestration engine. When
PhantomBuster (or any provider) reports that something happened (connection
sent, message delivered, reply received, etc.), the event arrives here as a
raw dict and goes through a multi-step pipeline.

PIPELINE (called via ingest_event()):
    1. Parse raw payload into (EventType, occurred_at, email, metadata)
    2. Identify the lead by email or lead_id from the prospects table
    3. Deduplicate rapid opens/clicks within SESSION_DEDUP_WINDOW_SECONDS
    4. Build an EngagementEvent and persist it (audit record — never rolled back)
    5. Look up the active LeadSequenceState for that lead
    6. Apply stop conditions via state_machine.apply_event_to_state()
       (e.g., positive reply → stop the whole sequence)
    7. Handle bounce key-account escalation (create urgent manual task)
    8. Classify replies (positive/negative) and persist Reply records
    9. Confirm SendLog rows as "sent" for email_sent events
    10. Sync prospect/sequence status columns for dashboard display
    11. Recompute engagement score and create high-score tasks if threshold crossed
    12. Run bounce-rate circuit breaker (auto-pause campaign if >10% bounce rate)

RESILIENCE (P4-2):
    Each sub-step after the event save runs in its own SAVEPOINT. A failure
    in any one sub-step rolls back only that sub-step, leaving the event
    record and all previously-succeeded sub-steps intact.

Edge cases handled:
    - Out-of-order events: occurred_at is taken from the payload, not ingestion time
    - LinkedIn accept after sequence end: log event + update lead flag + optional task
    - Rapid opens/clicks: deduplicated by SESSION_DEDUP_WINDOW_SECONDS
    - Bounce on key account: create urgent task
    - Duplicate webhook deliveries: reply dedup within 1 hour window
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .enums import EventType, OrchestrationStatus
from .models import EngagementEvent, IngestEventResponse, LeadContext
from .repositories import (
    AuditLogRepository,
    EngagementEventRepository,
    LeadSequenceStateRepository,
    ManualTaskRepository,
    ProspectRepository,
)
from .scorer import compute_score, should_create_task_for_score
from .state_machine import apply_event_to_state, is_terminal

logger = logging.getLogger(__name__)

# P1-7 / deep audit LOW-1: env-var overridable via SESSION_DEDUP_WINDOW_SECONDS.
from ..config import SESSION_DEDUP_WINDOW_SECONDS as SESSION_DEDUP_WINDOW_SECONDS


# ---------------------------------------------------------------------------
# Event type mapping  -  maps raw webhook event strings to typed EventType values.
# Works for PhantomBuster results and manual UI actions.
# ---------------------------------------------------------------------------

# Maps raw event strings from webhook payloads to our canonical EventType enum.
# Multiple raw strings can map to the same EventType because different providers
# use different naming conventions (e.g., "reply_received" and "email_replied"
# both map to email_replied_positive).
EVENT_TYPE_MAP: dict[str, EventType] = {
    # Generic event names (used by our internal system and manual UI actions)
    "email_sent":    EventType.email_sent,
    "email_opened":  EventType.email_opened,
    "email_clicked": EventType.email_clicked,
    "email_replied": EventType.email_replied_positive,  # negative requires manual classification
    "unsubscribed":  EventType.unsubscribed,
    # Distinguish hard vs soft bounce below in _parse_event_payload
    "email_bounced": EventType.email_bounced_hard,
    "email_link_clicked":   EventType.email_clicked,
    "reply_received":       EventType.email_replied_positive,
    "lead_unsubscribed":    EventType.unsubscribed,
    "campaign_completed":   EventType.email_sent,  # informational
    "lead_interested":      EventType.email_replied_positive,
    "lead_meeting_booked":  EventType.email_replied_positive,
    # LinkedIn events (from PhantomBuster results or manual UI actions)
    "linkedin_connect_sent":    EventType.linkedin_connect_sent,
    "linkedin_connect_accepted": EventType.linkedin_connect_accepted,
    "linkedin_message_sent":    EventType.linkedin_message_sent,
    "linkedin_message_replied": EventType.linkedin_message_replied,
    # PhantomBuster result event names
    "connection_request_sent":     EventType.linkedin_connect_sent,
    "connection_request_accepted": EventType.linkedin_connect_accepted,
    "message_sent":                EventType.linkedin_message_sent,
    "message_replied":             EventType.linkedin_message_replied,
}


# ---------------------------------------------------------------------------
# Payload parsers
# ---------------------------------------------------------------------------


def _parse_event_payload(payload: dict[str, Any]) -> tuple[EventType | None, datetime, str | None, dict]:
    """
    Parse a webhook payload into (event_type, occurred_at, email, metadata).

    Args:
        payload: Raw webhook dict from PhantomBuster or manual UI.

    Returns:
        Tuple of (EventType or None if unknown, occurred_at as UTC datetime,
        email address or None, metadata dict).  Caller must handle None event_type.
    """
    # Support multiple field names used by different providers and our own test payloads
    raw_type = payload.get("event_type") or payload.get("type") or payload.get("event") or ""
    # Fix 8: unknown event types return None instead of defaulting to email_sent
    event_type: EventType | None = EVENT_TYPE_MAP.get(raw_type)
    if event_type is None and raw_type not in ("email_bounced",):
        if raw_type:
            logger.warning("_parse_event_payload: unknown event type '%s'  -  will skip", raw_type)
        # Return None; caller will return early

    # Distinguish hard vs soft bounce using payload field "bounce_type"
    if raw_type == "email_bounced":
        bounce_type = payload.get("bounce_type", "hard")
        event_type = (
            EventType.email_bounced_hard if bounce_type == "hard"
            else EventType.email_bounced_soft
        )

    # Parse occurred_at from the payload; fall back to now (UTC)
    raw_ts = payload.get("timestamp") or payload.get("occurred_at") or payload.get("created_at")
    if raw_ts:
        try:
            if isinstance(raw_ts, (int, float)):
                occurred_at = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
            else:
                occurred_at = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        except (ValueError, OSError):
            occurred_at = datetime.now(timezone.utc)
    else:
        occurred_at = datetime.now(timezone.utc)

    email = payload.get("email") or payload.get("lead_email")
    provider = payload.get("provider") or "phantombuster"
    metadata = {
        "provider": provider,
        "event_raw_type": raw_type,
        "campaign_id": payload.get("campaign_id"),
        "sequence_step": payload.get("step"),
    }

    return event_type, occurred_at, email, metadata


# ---------------------------------------------------------------------------
# Open / click deduplication
# ---------------------------------------------------------------------------


def _is_duplicate_event(
    lead_id: str,
    event_type: EventType,
    occurred_at: datetime,
    event_repo: EngagementEventRepository,
) -> bool:
    """
    Return True if a recent event of the same type exists within the session window.

    Works for both email_opened and email_clicked events to suppress rapid
    duplicates caused by email-client prefetching, link scanners, etc.

    Args:
        lead_id:     Lead identifier.
        event_type:  The EventType to check (email_opened or email_clicked).
        occurred_at: Timestamp of the incoming event.
        event_repo:  Repository to query existing events.

    Returns:
        True if a duplicate event should be suppressed.
    """
    existing = event_repo.get_by_lead(lead_id, event_type)
    if not existing:
        return False

    window_start = occurred_at - timedelta(seconds=SESSION_DEDUP_WINDOW_SECONDS)
    for ev in existing:
        ts = ev.occurred_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if window_start <= ts <= occurred_at:
            logger.debug(
                "_is_duplicate_event: suppressing %s for lead=%s (existing at %s, window=%ds)",
                event_type.value,
                lead_id,
                ts.isoformat(),
                SESSION_DEDUP_WINDOW_SECONDS,
            )
            return True
    return False


# ---------------------------------------------------------------------------
# Lead lookup helper
# ---------------------------------------------------------------------------


def _build_lead_context(prospect: Any) -> LeadContext:
    """Build a LeadContext from a Prospect ORM row."""
    return LeadContext(
        lead_id=prospect.id,
        email=prospect.email,
        first_name=prospect.first_name,
        last_name=prospect.last_name or "",
        company=prospect.company or "",
        title=prospect.title or "",
        industry=prospect.industry or "",
        location=prospect.location or "",
        linkedin_url=prospect.linkedin_url,
        linkedin_connected=getattr(prospect, "linkedin_connected", False) or False,
        linkedin_connect_sent_at=getattr(prospect, "linkedin_connect_sent_at", None),
        tags=getattr(prospect, "tags", None) or [],
    )


# ---------------------------------------------------------------------------
# Savepoint-isolated sub-steps (P4-2)
# ---------------------------------------------------------------------------
#
# Previously the entire ingestion pipeline ran inside a single ``db.commit()``.
# A failure in any sub-step (reply classifier, score recompute, prospect sync,
# hard-bounce task, etc.) would roll back the whole transaction, including the
# audit-critical EngagementEvent row. That meant one flaky dependency could
# silently lose webhook history.
#
# The flow now is:
#
# 1. :func:`_save_event`  -  persists the EngagementEvent FIRST and commits.
#    This is the audit record; it must always succeed (and is never rolled
#    back by a later failure).
# 2. Every subsequent sub-step runs inside a SAVEPOINT via ``db.begin_nested``.
#    A failure in one savepoint rolls back only that savepoint, leaves the
#    event + previously-succeeded sub-steps intact, and logs at WARNING with
#    the ``event_id`` + ``lead_id`` so an operator can diagnose the failure
#    without losing work.
# 3. A final ``db.commit()`` flushes any savepoint writes that the
#    SQLAlchemy session is still holding in memory.
#
# Public surface: ``ingest_event(payload, db, provider)`` and the
# ``IngestEventResponse`` shape are unchanged  -  this is a purely mechanical
# hardening of the interior flow.


def _save_event(
    db: Session,
    event: EngagementEvent,
    event_repo: EngagementEventRepository,
) -> None:
    """Persist the EngagementEvent and commit immediately.

    This is the audit record for the webhook. It must always succeed and
    must never be rolled back by a later savepoint failure.
    """
    event_repo.save(event)
    db.commit()


def _apply_state_transition(
    db: Session,
    state_orm: Any,
    event: EngagementEvent,
    lead: LeadContext,
    audit_repo: AuditLogRepository,
    state_repo: LeadSequenceStateRepository,
    actions_triggered: list[str],
) -> None:
    """Apply stop/transition rules to the FSM and persist the new state."""
    if state_orm is None:
        return
    from .models import LeadSequenceState

    state = LeadSequenceState(
        state_id=state_orm.state_id,
        lead_id=state_orm.lead_id,
        sequence_id=state_orm.sequence_id,
        status=OrchestrationStatus(state_orm.status),
        current_step_index=state_orm.current_step_index,
        enrolled_at=state_orm.enrolled_at,
        scheduled_next_at=state_orm.scheduled_next_at,
        engagement_score=state_orm.engagement_score,
        stop_reason=state_orm.stop_reason,
        paused_reason=state_orm.paused_reason,
        completed_steps=state_orm.completed_steps or [],
    )

    status_before = state.status
    updated_state = apply_event_to_state(state, event, audit_repo)
    if updated_state.status != status_before:
        actions_triggered.append(
            f"state_transitioned_to_{updated_state.status.value}"
        )
    state_repo.upsert_from_pydantic(updated_state)


def _handle_hard_bounce_key_account(
    db: Session,
    lead: LeadContext,
    event_type: EventType,
    actions_triggered: list[str],
) -> None:
    """Create an urgent manual task when a key account hard-bounces."""
    if event_type != EventType.email_bounced_hard or not lead.is_key_account:
        return
    from .tasks import create_task_for_hard_bounce_key_account
    create_task_for_hard_bounce_key_account(lead=lead, db=db)
    actions_triggered.append("urgent_task_created_hard_bounce_key_account")


def _classify_and_create_reply(
    db: Session,
    payload: dict[str, Any],
    event_type: EventType,
    lead: LeadContext,
    occurred_at: datetime,
    email_from_payload: str | None,
    state_orm: Any,
    actions_triggered: list[str],
) -> None:
    """Classify a reply event and persist a ``Reply`` row."""
    if event_type not in (
        EventType.email_replied_positive,
        EventType.email_replied_negative,
    ):
        return

    import uuid as _uuid
    from ..db.models import Reply, EmailSequence as _ES

    reply_body = (
        payload.get("reply_body")
        or payload.get("body")
        or payload.get("detail", {}).get("reply_body", "")
        or "(no body captured)"
    )
    reply_subject = payload.get("subject") or payload.get("reply_subject") or ""

    # Find campaign_id and sequence_id from the active sequence. Prefer the
    # sequence linked to the orchestration state; fall back to the most
    # recently created sequence for this prospect.
    _campaign_id = None
    _sequence_id = None
    _seq = None
    if state_orm:
        _seq = (
            db.query(_ES)
            .filter_by(prospect_id=lead.lead_id)
            .filter(_ES.status.in_(["enrolled", "opened"]))
            .order_by(_ES.created_at.desc())
            .first()
        )
    if not _seq:
        _seq = (
            db.query(_ES)
            .filter_by(prospect_id=lead.lead_id)
            .order_by(_ES.created_at.desc())
            .first()
        )
    if _seq:
        _campaign_id = _seq.campaign_id
        _sequence_id = _seq.id

    _classification = (
        "positive"
        if event_type == EventType.email_replied_positive
        else "negative"
    )
    try:
        from ..services.reply_classifier import ReplyClassifier
        clf = ReplyClassifier()
        result = clf.classify(reply_body, reply_subject)
        _classification = result.get("category", _classification)
    except Exception as clf_err:
        logger.warning(
            "ingest_event: reply classification failed, using default: %s",
            clf_err,
        )

    # Dedup: skip if we already have a reply from this email for this prospect
    # within the last hour (handles duplicate webhook deliveries)
    _dedup_window = datetime.now(timezone.utc) - timedelta(hours=1)
    _existing = db.query(Reply).filter(
        Reply.prospect_id == lead.lead_id,
        Reply.from_email == email_from_payload,
        Reply.received_at > _dedup_window,
    ).first()
    if _existing:
        logger.info("ingest_event: reply dedup - skipping duplicate for lead=%s", lead.lead_id)
        return

    now = datetime.now(timezone.utc)
    reply_record = Reply(
        id=str(_uuid.uuid4()),
        prospect_id=lead.lead_id,
        campaign_id=_campaign_id,
        sequence_id=_sequence_id,
        subject=reply_subject,
        body=reply_body,
        from_email=email_from_payload,
        classification=_classification,
        received_at=occurred_at,
        classified_at=now,
    )
    db.add(reply_record)
    actions_triggered.append(f"reply_created_{_classification}")

    if _classification == "positive":
        from .tasks import create_task_for_positive_reply
        create_task_for_positive_reply(
            lead=lead,
            reply_id=reply_record.id,
            db=db,
        )
        actions_triggered.append("positive_reply_task_created")


def _confirm_send_log(
    db: Session,
    lead: LeadContext,
    event_type: EventType,
    occurred_at: datetime,
    metadata: dict,
    actions_triggered: list[str],
) -> None:
    """Mark the most recent SendLog row as ``sent`` on email_sent events."""
    if event_type != EventType.email_sent:
        return
    from ..db.models import EmailSequence as _ES, SendLog as _SL

    _sl_query = db.query(_SL).filter_by(prospect_id=lead.lead_id, status="pending")
    _send_log = _sl_query.order_by(_SL.step.asc()).first()
    if _send_log:
        _send_log.status = "sent"
        _send_log.sent_at = occurred_at
        actual_inbox = metadata.get("email_account")
        if actual_inbox:
            _send_log.inbox_email = actual_inbox
        actions_triggered.append("send_log_confirmed_sent")
    else:
        logger.debug(
            "ingest_event: no pending SendLog found for email_sent lead=%s",
            lead.lead_id,
        )

    # Bump EmailSequence.current_step for dashboard progress display.
    try:
        from ..config import MAX_SEQUENCE_EMAILS as _MSE
        _seq = db.query(_ES).filter_by(prospect_id=lead.lead_id).first()
        if _seq is not None:
            current = int(_seq.current_step or 1)
            if current < _MSE:
                _seq.current_step = current + 1
                actions_triggered.append("sequence_step_advanced")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ingest_event: failed to advance EmailSequence.current_step for lead=%s: %s",
            lead.lead_id, exc,
        )


def _sync_prospect_status(
    db: Session,
    prospect: Any,
    lead: LeadContext,
    event_type: EventType,
    occurred_at: datetime,
    email_from_payload: str | None,
    actions_triggered: list[str],
) -> None:
    """Mirror the event onto the Prospect + EmailSequence status columns."""
    from ..db.models import (
        EmailSequence,
        ProspectStatus,
        SequenceStatus,
        UnsubscribeEntry,
    )

    if event_type == EventType.email_opened:
        prospect.status = ProspectStatus.connected
        seqs = db.query(EmailSequence).filter_by(prospect_id=lead.lead_id).all()
        for s in seqs:
            if s.status == SequenceStatus.enrolled:
                s.status = SequenceStatus.connected
                s.opened_at = occurred_at
        actions_triggered.append("prospect_status_synced_opened")

    elif event_type in (
        EventType.email_replied_positive,
        EventType.email_replied_negative,
    ):
        prospect.status = ProspectStatus.replied
        seqs = db.query(EmailSequence).filter_by(prospect_id=lead.lead_id).all()
        for s in seqs:
            if s.status in (SequenceStatus.enrolled, SequenceStatus.opened, SequenceStatus.connected, SequenceStatus.messaged):
                s.status = SequenceStatus.replied
                s.replied_at = occurred_at
        actions_triggered.append("prospect_status_synced_replied")

    elif event_type in (
        EventType.email_bounced_hard,
        EventType.email_bounced_soft,
    ):
        prospect.status = ProspectStatus.bounced
        seqs = db.query(EmailSequence).filter_by(prospect_id=lead.lead_id).all()
        for s in seqs:
            if s.status in (SequenceStatus.enrolled, SequenceStatus.opened, SequenceStatus.connected, SequenceStatus.messaged):
                s.status = SequenceStatus.bounced
        # Mark pending email send_logs as bounced so they don't appear as
        # "waiting" in the UI. LinkedIn steps are tracked separately in the
        # linkedin_queue and orch state machine — they continue unaffected.
        from ..db.models import SendLog
        pending_sls = (
            db.query(SendLog)
            .filter(SendLog.prospect_id == lead.lead_id, SendLog.status == "pending")
            .all()
        )
        for sl in pending_sls:
            sl.status = "bounced"
        if pending_sls:
            logger.info(
                "bounce: marked %d pending send_log(s) as bounced for %s",
                len(pending_sls), prospect.email,
            )
        actions_triggered.append("prospect_status_synced_bounced")

    elif event_type == EventType.linkedin_connect_accepted:
        prospect.status = ProspectStatus.connected
        seqs = db.query(EmailSequence).filter_by(prospect_id=lead.lead_id).all()
        for s in seqs:
            if s.status in (SequenceStatus.enrolled,):
                s.status = SequenceStatus.connected
                s.opened_at = occurred_at
        actions_triggered.append("prospect_status_synced_connected")

    elif event_type == EventType.linkedin_message_sent:
        prospect.status = ProspectStatus.messaged
        seqs = db.query(EmailSequence).filter_by(prospect_id=lead.lead_id).all()
        for s in seqs:
            if s.status in (SequenceStatus.enrolled, SequenceStatus.connected):
                s.status = SequenceStatus.messaged
        actions_triggered.append("prospect_status_synced_messaged")

    elif event_type == EventType.unsubscribed:
        prospect.status = ProspectStatus.unsubscribed
        seqs = db.query(EmailSequence).filter_by(prospect_id=lead.lead_id).all()
        for s in seqs:
            if s.status not in (
                SequenceStatus.replied,
                SequenceStatus.bounced,
                SequenceStatus.unsubscribed,
                SequenceStatus.connected,
                SequenceStatus.messaged,
            ):
                s.status = SequenceStatus.unsubscribed
        email_lower = (email_from_payload or "").strip().lower()
        if email_lower:
            existing_unsub = (
                db.query(UnsubscribeEntry).filter_by(email=email_lower).first()
            )
            if not existing_unsub:
                db.add(
                    UnsubscribeEntry(
                        email=email_lower,
                        reason="webhook_unsubscribe",
                        source="webhook",
                    )
                )
        actions_triggered.append("prospect_status_synced_unsubscribed")


def _recompute_engagement_score(
    db: Session,
    state_orm: Any,
    prospect: Any,
    lead: LeadContext,
    event_type: EventType,
    event_repo: EngagementEventRepository,
    task_repo: ManualTaskRepository,
    actions_triggered: list[str],
) -> None:
    """Recompute the engagement score and create a high-score task if needed."""
    if state_orm is None:
        return

    new_score = compute_score(lead.lead_id, event_repo)
    if new_score != (state_orm.engagement_score or 0):
        state_orm.engagement_score = new_score
        # Sync score to prospect for dashboard visibility
        prospect.engagement_score = new_score
        actions_triggered.append(f"score_updated_{new_score}")

    try:
        from .config import get_sequence_config
        seq_config = get_sequence_config(state_orm.sequence_id)
        threshold = seq_config.scoring_threshold
    except KeyError:
        threshold = 5  # default

    if should_create_task_for_score(new_score, threshold, lead.lead_id, task_repo):
        from .tasks import create_task_for_high_score
        create_task_for_high_score(lead=lead, score=new_score, db=db)
        actions_triggered.append(f"high_score_task_created_score_{new_score}")


# ── Hardening 2.2: bounce-rate circuit breaker ──────────────────────────────


# Tunables. Reading from env at module-import time so an operator can
# tighten / loosen without a code change.
import os as _os  # noqa: E402  (deliberate late import)
_BOUNCE_RATE_THRESHOLD = float(_os.getenv("BOUNCE_RATE_THRESHOLD", "0.10"))  # 10%
_BOUNCE_RATE_MIN_SENDS = int(_os.getenv("BOUNCE_RATE_MIN_SENDS", "20"))      # don't fire on tiny samples
_BOUNCE_RATE_WINDOW_HOURS = int(_os.getenv("BOUNCE_RATE_WINDOW_HOURS", "24"))


def _check_bounce_rate_circuit_breaker(
    db: Session,
    lead: LeadContext,
    occurred_at: datetime,
    actions_triggered: list[str],
) -> None:
    """
    After a bounce event, recompute the campaign-level bounce rate
    over the last ``_BOUNCE_RATE_WINDOW_HOURS`` and auto-pause the
    campaign if it exceeds ``_BOUNCE_RATE_THRESHOLD`` with at least
    ``_BOUNCE_RATE_MIN_SENDS`` sends in the window.

    The min-sends floor avoids tripping on tiny samples (e.g. 1 send,
    1 bounce = 100% rate is not actionable).

    The pause is local-only  -  the campaign is marked paused in the DB so
    the watchdog flags it. Better to mark it paused locally than to silently
    keep it "active" while the bounce rate climbs.

    Surfaces in actions_triggered as ``bounce_circuit_breaker_paused_X_pct``
    so the watchdog / Slack alert path picks it up automatically.
    """
    from ..db.models import Campaign, CampaignStatus, SendLog

    # Find the prospect's most recent active campaign via SendLog  -
    # the lead-to-campaign mapping isn't on LeadContext.
    recent_log = (
        db.query(SendLog)
        .filter(SendLog.prospect_id == lead.lead_id)
        .order_by(SendLog.enrolled_at.desc())
        .first()
    )
    if not recent_log or not recent_log.campaign_id:
        return

    campaign = db.query(Campaign).filter_by(id=recent_log.campaign_id).first()
    if not campaign:
        return
    if campaign.status != CampaignStatus.active:
        return  # already paused  -  nothing to do

    # Window starts at occurred_at - N hours.
    window_start = occurred_at - timedelta(hours=_BOUNCE_RATE_WINDOW_HOURS)

    # Count sends in window for this campaign.
    sends_in_window = (
        db.query(func.count(SendLog.id))
        .filter(
            SendLog.campaign_id == campaign.id,
            SendLog.enrolled_at >= window_start,
            SendLog.status.in_(("sent", "bounced")),
        )
        .scalar()
    ) or 0

    if sends_in_window < _BOUNCE_RATE_MIN_SENDS:
        return  # sample too small to trip the breaker

    # Count bounces in window via the engagement events table  -  that's
    # the source of truth for bounce events; SendLog.status may not have
    # caught up yet on this exact request.
    from .orm import EngagementEventORM
    bounces_in_window = (
        db.query(func.count(EngagementEventORM.event_id))
        .join(SendLog, SendLog.prospect_id == EngagementEventORM.lead_id)
        .filter(
            SendLog.campaign_id == campaign.id,
            EngagementEventORM.event_type.in_(
                (EventType.email_bounced_hard.value, EventType.email_bounced_soft.value)
            ),
            EngagementEventORM.occurred_at >= window_start,
        )
        .scalar()
    ) or 0

    rate = bounces_in_window / sends_in_window
    if rate < _BOUNCE_RATE_THRESHOLD:
        return  # under threshold

    # TRIPPED  -  auto-pause the campaign locally.
    pct = round(rate * 100, 1)
    logger.error(
        "BOUNCE CIRCUIT BREAKER: campaign %s (%s) bounce rate %s%% "
        "(%d / %d) over %dh window  -  pausing.",
        campaign.id, campaign.name, pct, bounces_in_window,
        sends_in_window, _BOUNCE_RATE_WINDOW_HOURS,
    )
    campaign.status = CampaignStatus.paused

    actions_triggered.append(
        f"bounce_circuit_breaker_paused_{pct}_pct_{bounces_in_window}_of_{sends_in_window}"
    )


# Make ``func`` available for the bounce check above.
from sqlalchemy import func  # noqa: E402


# ---------------------------------------------------------------------------
# Ingestion sub-step helpers (extracted from ingest_event for readability)
# ---------------------------------------------------------------------------


def _identify_lead(
    payload: dict[str, Any],
    email_from_payload: str | None,
    db: Session,
    provider: str,
    occurred_at: datetime,
) -> tuple[Any, ProspectRepository] | IngestEventResponse:
    """Resolve the prospect from the payload by ID or email.

    Returns a (prospect, prospect_repo) tuple on success, or an
    IngestEventResponse early-exit when the lead cannot be identified.
    """
    lead_id_from_payload = payload.get("lead_id") or payload.get("prospect_id")
    prospect_repo = ProspectRepository(db)
    prospect = None

    if lead_id_from_payload:
        prospect = prospect_repo.get(str(lead_id_from_payload))

    if prospect is None and email_from_payload:
        prospect = prospect_repo.get_by_email(str(email_from_payload))

    if prospect is None:
        logger.warning(
            "ingest_event: cannot identify lead from payload (email=%s lead_id=%s)",
            email_from_payload,
            lead_id_from_payload,
        )
        placeholder_id = f"unidentified_{provider}_{occurred_at.timestamp()}"
        return IngestEventResponse(
            event_id=placeholder_id,
            lead_id="unknown",
            actions_triggered=["lead_not_found"],
        )

    return prospect, prospect_repo


def _check_dedup(
    lead_id: str,
    event_type: EventType,
    occurred_at: datetime,
    event_repo: EngagementEventRepository,
) -> IngestEventResponse | None:
    """Return an early-exit response if this open/click is a duplicate, else None."""
    if event_type not in (EventType.email_opened, EventType.email_clicked):
        return None

    if _is_duplicate_event(lead_id, event_type, occurred_at, event_repo):
        label = "open" if event_type == EventType.email_opened else "click"
        logger.debug(
            "ingest_event: suppressing duplicate %s for lead=%s",
            label,
            lead_id,
        )
        return IngestEventResponse(
            event_id=f"suppressed_duplicate_{label}",
            lead_id=lead_id,
            actions_triggered=[f"{label}_deduplicated"],
        )
    return None


def _handle_connect_accepted_after_sequence(
    db: Session,
    event: EngagementEvent,
    event_repo: EngagementEventRepository,
    lead: LeadContext,
    actions_triggered: list[str],
) -> IngestEventResponse:
    """Log a linkedin_connect_accepted event when the sequence is already terminal.

    Returns an IngestEventResponse for early exit from the parent.
    """
    logger.info(
        "ingest_event: linkedin_connect_accepted after sequence end for lead=%s  -  logging only",
        lead.lead_id,
    )
    try:
        _save_event(db, event, event_repo)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "ingest_event: commit failed saving linkedin_connect_accepted event: %s",
            exc,
        )
        db.rollback()
    return IngestEventResponse(
        event_id=event.event_id,
        lead_id=lead.lead_id,
        actions_triggered=actions_triggered,
    )


def _run_savepoint_substeps(
    db: Session,
    event: EngagementEvent,
    event_type: EventType,
    lead: LeadContext,
    prospect: Any,
    payload: dict[str, Any],
    occurred_at: datetime,
    email_from_payload: str | None,
    metadata: dict,
    state_orm: Any,
    audit_repo: AuditLogRepository,
    state_repo: LeadSequenceStateRepository,
    task_repo: ManualTaskRepository,
    event_repo: EngagementEventRepository,
    actions_triggered: list[str],
) -> None:
    """Execute all savepoint-isolated sub-steps after the event is persisted.

    Each sub-step runs inside its own SAVEPOINT so a failure in one does
    not roll back the audit event or previously-succeeded sub-steps.
    """

    def _safe(label: str, fn, *args, **kwargs) -> None:
        """Run ``fn`` inside a SAVEPOINT and log WARNING on failure."""
        try:
            with db.begin_nested():
                fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ingest_event[%s]: savepoint failed for event=%s lead=%s: %s",
                label,
                event.event_id,
                lead.lead_id,
                exc,
                exc_info=True,
            )

    def _safe_with_stale_retry(label: str, fn, *args, **kwargs) -> None:
        """SWAT-C5: like _safe but retries once on StaleDataError (optimistic lock conflict)."""
        for attempt in range(2):
            try:
                with db.begin_nested():
                    fn(*args, **kwargs)
                return
            except StaleDataError:
                if attempt == 0:
                    logger.warning(
                        "ingest_event[%s]: StaleDataError for event=%s lead=%s — retrying",
                        label, event.event_id, lead.lead_id,
                    )
                    if state_orm is not None:
                        try:
                            db.refresh(state_orm)
                        except Exception:
                            pass
                    continue
                logger.error(
                    "ingest_event[%s]: StaleDataError RETRY FAILED for event=%s lead=%s — state_transition_lost",
                    label, event.event_id, lead.lead_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ingest_event[%s]: savepoint failed for event=%s lead=%s: %s",
                    label, event.event_id, lead.lead_id, exc, exc_info=True,
                )
                return

    _safe_with_stale_retry(
        "state_transition",
        _apply_state_transition,
        db, state_orm, event, lead, audit_repo, state_repo, actions_triggered,
    )
    _safe(
        "hard_bounce_key_account",
        _handle_hard_bounce_key_account,
        db, lead, event_type, actions_triggered,
    )
    _safe(
        "classify_reply",
        _classify_and_create_reply,
        db, payload, event_type, lead, occurred_at, email_from_payload,
        state_orm, actions_triggered,
    )
    _safe(
        "confirm_send_log",
        _confirm_send_log,
        db, lead, event_type, occurred_at, metadata, actions_triggered,
    )
    _safe(
        "sync_prospect_status",
        _sync_prospect_status,
        db, prospect, lead, event_type, occurred_at, email_from_payload,
        actions_triggered,
    )
    _safe(
        "recompute_score",
        _recompute_engagement_score,
        db, state_orm, prospect, lead, event_type, event_repo, task_repo,
        actions_triggered,
    )
    # Hardening 2.2: bounce-rate circuit breaker. After every bounce
    # event, recompute the campaign-level bounce rate over the last 24
    # hours. If it exceeds 10% with at least 20 sends in the window,
    # auto-pause the campaign and surface in actions_triggered so the
    # watchdog/Slack alert path picks it up. Only fires on bounce
    # events so the savepoint isn't paying for it on every open/click.
    if event_type in (EventType.email_bounced_hard, EventType.email_bounced_soft):
        _safe(
            "bounce_circuit_breaker",
            _check_bounce_rate_circuit_breaker,
            db, lead, occurred_at, actions_triggered,
        )


# ---------------------------------------------------------------------------
# Main ingestion entry point
# ---------------------------------------------------------------------------


def ingest_event(
    payload: dict[str, Any],
    db: Session,
    provider: str = "phantombuster",
) -> IngestEventResponse:
    """
    Ingest a raw webhook event payload from a LinkedIn provider.

    After P4-2 the ingestion flow uses savepoints so a failure in any
    individual sub-step (reply classifier, score recompute, prospect sync,
    hard-bounce task, etc.) no longer rolls back the EngagementEvent audit
    row or the state transition. Each sub-step logs at WARNING on failure
    and the caller always sees the event_id.

    Args:
        payload:  Raw dict from the provider webhook.
        db:       SQLAlchemy session  -  caller controls transaction.
        provider: Provider identifier string (default: "phantombuster").

    Returns:
        IngestEventResponse with the event_id, lead_id, and actions triggered.
    """
    actions_triggered: list[str] = []

    import uuid

    # ── 1. Parse payload & skip unknown event types ───────────────────────────
    event_type, occurred_at, email_from_payload, metadata = _parse_event_payload(payload)

    if event_type is None:
        return IngestEventResponse(
            event_id=str(uuid.uuid4()),
            lead_id="unknown",
            actions_triggered=["skipped_unknown_event_type"],
        )

    # ── 2. Identify lead ──────────────────────────────────────────────────────
    result = _identify_lead(payload, email_from_payload, db, provider, occurred_at)
    if isinstance(result, IngestEventResponse):
        return result
    prospect, prospect_repo = result

    lead = _build_lead_context(prospect)
    event_repo = EngagementEventRepository(db)

    # ── 3. Deduplicate rapid opens and clicks ────────────────────────────────
    dedup_response = _check_dedup(lead.lead_id, event_type, occurred_at, event_repo)
    if dedup_response is not None:
        return dedup_response

    # ── 4. Build EngagementEvent ───────────────────────────────────────────────
    event = EngagementEvent(
        lead_id=lead.lead_id,
        event_type=event_type,
        occurred_at=occurred_at,
        metadata=metadata,
        raw_payload=payload,
    )

    # ── 5. Look up active sequence state ─────────────────────────────────────
    state_repo = LeadSequenceStateRepository(db)
    audit_repo = AuditLogRepository(db)
    task_repo = ManualTaskRepository(db)

    state_orm = state_repo.get_active_for_lead(lead.lead_id)

    # ── 6. Handle LinkedIn connect_accepted flag (side-effects the prospect) ──
    if event_type == EventType.linkedin_connect_accepted:
        prospect_repo.update_linkedin_connected(
            lead_id=lead.lead_id,
            connected=True,
        )
        actions_triggered.append("linkedin_connected_flag_set")

        if state_orm is None or is_terminal(OrchestrationStatus(state_orm.status)):
            return _handle_connect_accepted_after_sequence(
                db, event, event_repo, lead, actions_triggered,
            )

    # ── 7. Save the event + commit  -  audit record, never rolled back ─────────
    try:
        _save_event(db, event, event_repo)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "ingest_event: failed to persist audit event for lead=%s: %s",
            lead.lead_id,
            exc,
        )
        db.rollback()
        raise
    actions_triggered.append(f"event_saved_{event_type.value}")

    # ── 8. Savepoint-isolated sub-steps ──────────────────────────────────────
    _run_savepoint_substeps(
        db, event, event_type, lead, prospect, payload,
        occurred_at, email_from_payload, metadata,
        state_orm, audit_repo, state_repo, task_repo, event_repo,
        actions_triggered,
    )

    # ── 9. Final commit ───────────────────────────────────────────────────────
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "ingest_event: final commit failed for lead=%s: %s",
            lead.lead_id,
            exc,
        )
        db.rollback()
        raise

    logger.info(
        "ingest_event: lead=%s event=%s actions=%s",
        lead.lead_id,
        event_type.value,
        actions_triggered,
    )

    return IngestEventResponse(
        event_id=event.event_id,
        lead_id=lead.lead_id,
        actions_triggered=actions_triggered,
    )
