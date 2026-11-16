"""
LinkedIn batch worker  -  the background thread that drives PhantomBuster.

This is the bridge between the orchestration engine and PhantomBuster (PB).
The orchestration handlers queue LinkedIn actions (connect requests, messages)
to the linkedin_queue table. This worker runs in a dedicated background thread
and handles the entire lifecycle:

  1. DRAIN: picks up "queued" items, validates them, marks them "processing"
  2. FEED: processing items become visible in CSV feed endpoints
     (/api/pb/connect-leads.csv, /api/pb/message-leads.csv)
  3. LAUNCH: explicitly launches PB phantoms per-user with correct identity
  4. POLL: checks PB's cumulative result for each agent to confirm delivery
  5. ACCEPT: checks PB results for connection acceptances (1st degree)
  6. HEALTH: detects silent failures (0-row runs, high message failure rates)

THE CSV FEED ARCHITECTURE (how PB gets our leads):
  - PB phantoms are configured with a "spreadsheet URL" pointing to our CSV endpoints
  - The worker marks queue items as "processing"; the CSV endpoint serves all
    processing items as CSV rows
  - PB reads the CSV on each launch and processes the URLs at its own pace
  - The worker polls PB's result API to confirm which leads were processed

WHY THIS EXISTS (Phase 2 / P2-3):
  The deep audit found three problems with the original design:
    1. CRITICAL-1: flush_linkedin_queue blocked the scheduler tick for minutes
    2. CRITICAL-1: shutdown couldn't drain; processing rows got stuck forever
    3. HIGH-8: items from different users were mixed into one Phantom launch

  This module fixes all three by running independently of the scheduler with
  per-user grouping and graceful shutdown.

PUBLIC API:
  - start_linkedin_worker(): call once from main.py lifespan startup
  - stop_linkedin_worker(timeout=30): call once from lifespan shutdown
  - linkedin_worker_alive(): for the watchdog liveness check
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..config import (
    LINKEDIN_BATCH_SIZE_LIMIT,
    LINKEDIN_CONNECT_TIMEOUT_HOURS,
    LINKEDIN_MAX_AUTO_RETRIES,
    LINKEDIN_MESSAGE_TIMEOUT_HOURS,
    LINKEDIN_POLL_INTERVAL_SECONDS,
    LINKEDIN_POLL_TIMEOUT_SECONDS,
    LINKEDIN_WORKER_INTERVAL_SECONDS,
    LINKEDIN_WORKER_RECONCILE_MINUTES,
)

logger = logging.getLogger(__name__)


# ── Module state (single worker per process) ──────────────────────────────
_worker_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
# Timestamp of the last completed worker cycle. Read by the health endpoint
# to determine if the worker is actively cycling or silently stuck.
_last_cycle_at: Optional[datetime] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Public lifecycle API ──────────────────────────────────────────────────


def linkedin_worker_alive() -> bool:
    """Return True if the worker thread is currently running."""
    return _worker_thread is not None and _worker_thread.is_alive()


def _validate_pb_agents() -> bool:
    """Check that configured PB agent IDs are valid before starting the worker.

    Reads PHANTOMBUSTER_CONNECT_AGENT_ID and PHANTOMBUSTER_MESSAGE_AGENT_ID
    from the environment, then tries to fetch each agent's info via the PB API.
    Returns True if both are valid (or not configured), False if any configured
    agent ID is invalid. This prevents the worker from running cycles that will
    fail on every launch attempt.
    """
    from .phantombuster import PhantomBusterClient, CircuitOpenError

    api_key = os.getenv("PHANTOMBUSTER_API_KEY", "").strip()
    if not api_key:
        logger.warning("_validate_pb_agents: PHANTOMBUSTER_API_KEY not set, skipping validation")
        return True  # Can't validate without a key, let the worker try anyway

    agent_ids = {
        "PHANTOMBUSTER_CONNECT_AGENT_ID": os.getenv("PHANTOMBUSTER_CONNECT_AGENT_ID", "").strip(),
        "PHANTOMBUSTER_MESSAGE_AGENT_ID": os.getenv("PHANTOMBUSTER_MESSAGE_AGENT_ID", "").strip(),
    }

    try:
        client = PhantomBusterClient(api_key=api_key)
    except Exception as exc:
        logger.critical("_validate_pb_agents: cannot create PB client: %s", exc)
        return False

    all_valid = True
    for env_name, agent_id in agent_ids.items():
        if not agent_id:
            continue  # Not configured — skip
        try:
            info = client.get_agent(agent_id)
            agent_name = info.get("name", "unknown")
            logger.info(
                "_validate_pb_agents: %s=%s is valid (name=%s)",
                env_name, agent_id, agent_name,
            )
        except CircuitOpenError:
            logger.warning("_validate_pb_agents: circuit open, skipping validation")
            return True  # Can't validate right now, don't block startup
        except Exception as exc:
            logger.critical(
                "_validate_pb_agents: %s=%s is INVALID: %s. "
                "Fix the agent ID in .env or the PB UI.",
                env_name, agent_id, exc,
            )
            all_valid = False

    try:
        client.close()
    except Exception:
        pass
    return all_valid


def start_linkedin_worker() -> None:
    """Start the LinkedIn worker thread.

    Idempotent: a second call while a worker is alive is a no-op. The
    worker is a daemon thread so it dies with the process if the lifespan
    shutdown handler doesn't get a chance to drain it.

    Pre-launch validation: checks that configured PB agent IDs are valid
    before starting. If validation fails, the worker is NOT started and a
    CRITICAL log is emitted.
    """
    global _worker_thread
    if linkedin_worker_alive():
        logger.debug("linkedin_worker: already running, ignoring start request")
        return

    # Pre-launch: validate PB agent IDs are real before spinning up the thread
    if not _validate_pb_agents():
        logger.critical(
            "LinkedIn worker disabled: PB agent IDs are invalid. "
            "Fix PHANTOMBUSTER_CONNECT_AGENT_ID / PHANTOMBUSTER_MESSAGE_AGENT_ID "
            "in .env and restart the service."
        )
        return

    _stop_event.clear()
    # Reconcile stuck `processing` rows from a prior crashed process before
    # the worker loop starts pulling new work  -  otherwise those rows would
    # sit forever (the loop only looks at `queued`).
    try:
        _reconcile_stuck_processing()
    except Exception as exc:  # noqa: BLE001
        logger.error("linkedin_worker: reconciliation at startup failed: %s", exc)

    _worker_thread = threading.Thread(
        target=_worker_loop, name="linkedin-worker", daemon=True
    )
    _worker_thread.start()
    logger.info(
        "linkedin_worker: started (interval=%ds, reconcile_after=%dm)",
        LINKEDIN_WORKER_INTERVAL_SECONDS, LINKEDIN_WORKER_RECONCILE_MINUTES,
    )


def stop_linkedin_worker(timeout: float = 30.0) -> None:
    """Signal the worker to stop and wait up to `timeout` seconds for it.

    The worker checks `_stop_event` between every `time.sleep` inside the
    Phantom poll loop, so a long poll yields within a few seconds of the
    signal. If the worker doesn't drain in time we log and let the process
    exit anyway  -  systemd will SIGKILL after its own TimeoutStopSec.
    """
    global _worker_thread
    if not linkedin_worker_alive():
        return
    _stop_event.set()
    logger.info("linkedin_worker: stop signal sent")
    _worker_thread.join(timeout=timeout)
    if _worker_thread.is_alive():
        logger.warning(
            "linkedin_worker: did not drain within %.0fs  -  exiting anyway. "
            "Any in-flight Phantom poll will be abandoned. The worker's "
            "startup reconcile on next boot will reset the stuck row.",
            timeout,
        )
    else:
        logger.info("linkedin_worker: drained cleanly")
    _worker_thread = None


# ── Worker loop ───────────────────────────────────────────────────────────


def _worker_loop() -> None:
    """Main worker loop: drain queue, poll PB results, sleep, repeat.

    Each cycle runs five phases sequentially. Each phase is wrapped in its own
    try/except so a crash in one phase doesn't skip the others. The _stop_event
    is checked between phases for graceful shutdown.

    The cycle interval (5+ minutes) matches PhantomBuster's processing rate
    (~20 profiles/day). Polling more often wastes API calls without benefit.
    """
    global _last_cycle_at
    from ..db.database import SessionLocal

    cycle = 0
    while not _stop_event.is_set():
        cycle += 1
        logger.info("linkedin_worker: cycle %d start", cycle)
        db = SessionLocal()
        try:
            # Phase 1: DRAIN — validate queued items, mark as "processing".
            # Processing items become visible in the CSV feed for PB to fetch.
            try:
                _drain_once(db)
            except Exception as exc:  # noqa: BLE001
                logger.error("linkedin_worker: drain crashed: %s", exc, exc_info=True)
                try:
                    db.rollback()
                except Exception:
                    pass

            # Phase 1b: LAUNCH — explicitly launch PB phantoms for each user
            # that has processing items. Passes per-user identity_id so PB uses
            # the correct LinkedIn account in multi-user deployments.
            if not _stop_event.is_set():
                try:
                    _launch_pending_phantoms(db)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("linkedin_worker: launch_pending crashed: %s", exc)

            # Phase 2: POLL — check PB's cumulative result for each agent.
            # Matches processing items against PB result rows by normalised
            # LinkedIn URL to confirm delivery (connect sent, message delivered).
            if not _stop_event.is_set():
                try:
                    poll_pb_results(db)
                except Exception as exc:  # noqa: BLE001
                    logger.error("linkedin_worker: poll_pb_results crashed: %s", exc, exc_info=True)
                    try:
                        db.rollback()
                    except Exception:
                        pass

            # Phase 3: ACCEPT — scan PB connect results for connection acceptances.
            # When a prospect accepts our connection (degree="1st"), mark them as
            # linkedin_connected=True so the message step condition passes.
            # Also queues retroactive messages if the sequence already passed the
            # message step before the connection was accepted.
            if not _stop_event.is_set():
                try:
                    check_connection_acceptances(db)
                except Exception as exc:  # noqa: BLE001
                    logger.error("linkedin_worker: check_acceptances crashed: %s", exc, exc_info=True)
                    try:
                        db.rollback()
                    except Exception:
                        pass

            # Phase 4: HEALTH — detect silent failures before they age out.
            # Checks for: (a) phantom runs that processed 0 leads, (b) high
            # message failure rates in the last 72h.
            if not _stop_event.is_set():
                try:
                    _check_linkedin_health(db)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("linkedin_worker: health_check crashed: %s", exc)

            # Phase 5: STUCK PROCESSING — detect and requeue items that have
            # been stuck in "processing" for too long. This catches items that
            # fell through the cracks (e.g. PB lost the container, network
            # partition during launch).
            if not _stop_event.is_set():
                try:
                    _check_stuck_processing(db)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("linkedin_worker: stuck_processing check crashed: %s", exc)

            # Phase 6: PB SYNC — re-discover phantoms and sync identities.
            # Runs every 10th cycle (~50 min) so PB changes are picked up
            # automatically without needing a service restart.
            if not _stop_event.is_set() and cycle % 10 == 0:
                try:
                    from ..api.linkedin_accounts import _auto_sync_from_pb
                    _auto_sync_from_pb(db)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("linkedin_worker: PB auto-sync: %s", exc)
                try:
                    _sync_account_identities(db)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("linkedin_worker: identity sync: %s", exc)
        finally:
            try:
                db.close()
            except Exception:
                pass

        # Record cycle completion time for the health endpoint
        _last_cycle_at = _utcnow()

        # Poll every 5 minutes minimum. PB processes ~20 leads/day (~1 per 72 min).
        # Aggressive polling wastes API calls without speeding up sends.
        poll_interval = max(LINKEDIN_WORKER_INTERVAL_SECONDS, 300)
        logger.info("linkedin_worker: cycle %d done, sleeping %ds", cycle, poll_interval)
        # _stop_event.wait() returns True if the event was set (shutdown signal),
        # allowing us to break out of the sleep early on SIGTERM.
        if _stop_event.wait(timeout=poll_interval):
            return


# ── Reconciliation ────────────────────────────────────────────────────────


def _get_retry_count(q) -> int:
    """Extract how many auto-retries this queue item has already had.

    Convention: result_detail starts with ``auto_retry_N`` after the Nth
    retry. Items that have never been retried return 0.
    """
    detail = q.result_detail or ""
    if detail.startswith("auto_retry_"):
        try:
            return int(detail.split("_")[2].split(" ")[0])
        except (IndexError, ValueError):
            return 1
    return 0


def _reconcile_stuck_processing() -> None:
    """Reset orphaned `processing` rows so they can be retried.

    Only resets rows that have NO phantom_container_id - these were mid-launch
    when the process crashed. Rows WITH a container_id are already registered
    in PB's internal queue and will be resolved by the poller checking the
    agent's cumulative result. Resetting those would cause redundant launches.
    """
    from ..db.database import SessionLocal
    from ..db.models import LinkedInQueue, LinkedInQueueStatus

    cutoff = _utcnow() - timedelta(minutes=LINKEDIN_WORKER_RECONCILE_MINUTES)
    db = SessionLocal()
    try:
        from sqlalchemy import func as _f
        # Reset rows WITHOUT a container_id (mid-launch crash).
        stuck_no_cid = (
            db.query(LinkedInQueue)
            .filter(
                LinkedInQueue.status == LinkedInQueueStatus.processing,
                LinkedInQueue.phantom_container_id.is_(None),
                _f.coalesce(LinkedInQueue.processed_at, LinkedInQueue.created_at) < cutoff,
            )
            .all()
        )
        # Also reset rows WITH a container_id that are older than 3 days.
        # These have dead containers (PB lost them or they were cleaned up).
        dead_cutoff = _utcnow() - timedelta(days=3)
        stuck_dead_cid = (
            db.query(LinkedInQueue)
            .filter(
                LinkedInQueue.status == LinkedInQueueStatus.processing,
                LinkedInQueue.phantom_container_id.isnot(None),
                _f.coalesce(LinkedInQueue.processed_at, LinkedInQueue.created_at) < dead_cutoff,
            )
            .all()
        )
        stuck = stuck_no_cid + stuck_dead_cid
        if not stuck:
            return
        for q in stuck:
            q.status = LinkedInQueueStatus.queued
        db.commit()
        logger.warning(
            "linkedin_worker: reset %d orphaned `processing` row(s) to `queued` "
            "(%d no container_id >%dm, %d dead container_id >3d)",
            len(stuck), len(stuck_no_cid), LINKEDIN_WORKER_RECONCILE_MINUTES, len(stuck_dead_cid),
        )
    finally:
        db.close()


# ── Health monitoring ─────────────────────────────────────────────────────

# SWAT-C4: Tracks container_ids launched by _launch_pending_phantoms so we can
# verify them in the next poll cycle.
# Key is (user_id, agent_id) to prevent cross-user requeue on 0-row health checks.
_launched_containers: dict[tuple[str | None, str], tuple[str, float, int]] = {}


def _check_linkedin_health(db: Session) -> None:
    """Detect silent LinkedIn failures and surface them as ERROR logs.

    Runs every worker cycle. Checks two failure modes:

    1. **Launched container processed 0 leads**: phantom ran but got nothing
       from the CSV feed. Indicates a URL/bonus-argument mismatch or PB's
       internal "already processed" cache blocking new leads. Re-queues
       all processing items for that agent so the next cycle re-launches.

    2. **High message failure rate**: >50% of message items created in the
       last 72h are failed. Fires an ERROR so it surfaces in logs/monitoring.
       Does NOT auto-reset here — auto-retry in poll_pb_results handles that.
    """
    from ..db.models import LinkedInQueue, LinkedInQueueStatus
    from sqlalchemy import func as _f

    now = _utcnow()

    # SWAT-L4: evict entries older than 2h to prevent unbounded memory growth
    _ttl_cutoff = now.timestamp() - 7200
    for key in list(_launched_containers):
        _, launch_ts, _ = _launched_containers[key]
        if launch_ts < _ttl_cutoff:
            _launched_containers.pop(key, None)

    # ── Check 1: verify launched containers ───────────────────────────────
    # SWAT-C4: track stale agents as (user_id, agent_id) tuples for scoped requeue
    stale_agents: list[tuple[str | None, str]] = []
    for (uid, agent_id), (container_id, launch_ts, expected_count) in list(_launched_containers.items()):
        age_min = (now.timestamp() - launch_ts) / 60
        if age_min < 5:
            continue  # Too soon — PB hasn't finished yet
        try:
            # Re-use the global PB client (agent_id is workspace-scoped)
            import os as _os
            from ..services.phantombuster import PhantomBusterClient
            pb = PhantomBusterClient(api_key=_os.getenv("PHANTOMBUSTER_API_KEY"))
            container = pb.get_container(container_id)
            status = container.get("status", "")
            if status not in ("finished", "error"):
                continue  # Still running
            # Container done — check how many rows it produced
            result = pb.get_container_result(container_id)
            result_obj = result.get("resultObject") if isinstance(result, dict) else None
            if isinstance(result_obj, str):
                import json as _json
                try:
                    result_obj = _json.loads(result_obj)
                except Exception:
                    result_obj = None
            rows_produced = len(result_obj) if isinstance(result_obj, list) else 0
            if rows_produced == 0 and expected_count > 0:
                logger.error(
                    "LINKEDIN HEALTH: phantom container %s finished but produced 0 rows "
                    "despite %d queued items for user=%s agent=%s. Re-queueing.",
                    container_id, expected_count, uid or "global", agent_id,
                )
                stale_agents.append((uid, agent_id))
        except Exception as exc:
            logger.warning("_check_linkedin_health: container check failed for (%s,%s): %s", uid, agent_id, exc)
        finally:
            _launched_containers.pop((uid, agent_id), None)

    # SWAT-C4: re-queue only items belonging to the specific stale user, not globally
    for stale_uid, _stale_aid in stale_agents:
        q_filter = db.query(LinkedInQueue).filter(
            LinkedInQueue.status == LinkedInQueueStatus.processing,
        )
        if stale_uid:
            q_filter = q_filter.filter(LinkedInQueue.user_id == stale_uid)
        else:
            q_filter = q_filter.filter(LinkedInQueue.user_id.is_(None))
        requeued = q_filter.all()
        for q in requeued:
            q.status = LinkedInQueueStatus.queued
            q.processed_at = None
            q.result_detail = "requeued: phantom ran but produced 0 rows"
        if requeued:
            db.commit()
            logger.warning(
                "_check_linkedin_health: re-queued %d items for user=%s after 0-row phantom run",
                len(requeued), stale_uid or "global",
            )

    # ── Check 2: high message failure rate in last 72h ────────────────────
    cutoff = now - timedelta(hours=72)
    total_msg = (
        db.query(_f.count(LinkedInQueue.id))
        .filter(
            LinkedInQueue.action_type == "message",
            LinkedInQueue.created_at >= cutoff,
        )
        .scalar() or 0
    )
    failed_msg = (
        db.query(_f.count(LinkedInQueue.id))
        .filter(
            LinkedInQueue.action_type == "message",
            LinkedInQueue.status == LinkedInQueueStatus.failed,
            LinkedInQueue.created_at >= cutoff,
        )
        .scalar() or 0
    )
    if total_msg > 0 and failed_msg / total_msg > 0.5:
        logger.error(
            "LINKEDIN HEALTH: %d/%d message queue items failed in the last 72h (%.0f%%). "
            "PhantomBuster message phantom is not confirming sends. "
            "Check: (1) PB phantom logs, (2) CSV feed at /api/pb/message-leads.csv, "
            "(3) LinkedIn session cookie expiry in PhantomBuster UI.",
            failed_msg, total_msg, 100 * failed_msg / total_msg,
        )


# ── Per-cycle stuck processing detection (Phase 5) ───────────────────────


def _check_stuck_processing(db: Session) -> None:
    """Detect and handle items stuck in 'processing' for too long.

    Runs every worker cycle (Phase 5). Three tiers of staleness:

    1. NO phantom_container_id AND >2 hours old: requeue to "queued".
       These were likely mid-launch when something went wrong.

    2. WITH phantom_container_id AND >24 hours old: requeue with a note.
       The PB container is stale — it ran but results were never matched.

    3. Any processing item >72 hours old regardless: permanently fail.
       Something is fundamentally wrong with this item.
    """
    from ..db.models import LinkedInQueue, LinkedInQueueStatus
    from sqlalchemy import func as _f

    now = _utcnow()
    requeued_count = 0
    failed_count = 0

    # Tier 3 first: permanently fail anything >72h (highest priority)
    cutoff_72h = now - timedelta(hours=72)
    stuck_72h = (
        db.query(LinkedInQueue)
        .filter(
            LinkedInQueue.status == LinkedInQueueStatus.processing,
            _f.coalesce(LinkedInQueue.processed_at, LinkedInQueue.created_at) < cutoff_72h,
        )
        .all()
    )
    for q in stuck_72h:
        q.status = LinkedInQueueStatus.failed
        q.result_detail = "permanently_failed: stuck processing >72h"
        failed_count += 1
        _pause_orch_for_failed_linkedin(db, q.prospect_id, q.action_type)

    if stuck_72h:
        db.commit()

    # Tier 2: WITH container_id AND >24h → requeue
    cutoff_24h = now - timedelta(hours=24)
    stuck_24h = (
        db.query(LinkedInQueue)
        .filter(
            LinkedInQueue.status == LinkedInQueueStatus.processing,
            LinkedInQueue.phantom_container_id.isnot(None),
            _f.coalesce(LinkedInQueue.processed_at, LinkedInQueue.created_at) < cutoff_24h,
            # Exclude the >72h items we just failed
            _f.coalesce(LinkedInQueue.processed_at, LinkedInQueue.created_at) >= cutoff_72h,
        )
        .all()
    )
    for q in stuck_24h:
        q.status = LinkedInQueueStatus.queued
        q.processed_at = None
        q.result_detail = "requeued: stale processing >24h"
        requeued_count += 1

    if stuck_24h:
        db.commit()

    # Tier 1: NO container_id AND >2h → requeue
    cutoff_2h = now - timedelta(hours=2)
    stuck_2h = (
        db.query(LinkedInQueue)
        .filter(
            LinkedInQueue.status == LinkedInQueueStatus.processing,
            LinkedInQueue.phantom_container_id.is_(None),
            _f.coalesce(LinkedInQueue.processed_at, LinkedInQueue.created_at) < cutoff_2h,
            # Exclude items already handled by tiers 2 and 3
            _f.coalesce(LinkedInQueue.processed_at, LinkedInQueue.created_at) >= cutoff_24h,
        )
        .all()
    )
    for q in stuck_2h:
        q.status = LinkedInQueueStatus.queued
        q.processed_at = None
        q.result_detail = "requeued: no container_id and stuck >2h"
        requeued_count += 1

    if stuck_2h:
        db.commit()

    if requeued_count or failed_count:
        logger.info(
            "_check_stuck_processing: requeued=%d, permanently_failed=%d "
            "(tier1_no_cid_2h=%d, tier2_stale_cid_24h=%d, tier3_72h=%d)",
            requeued_count, failed_count,
            len(stuck_2h), len(stuck_24h), len(stuck_72h),
        )
    if requeued_count:
        logger.warning(
            "_check_stuck_processing: %d items were stuck in processing and requeued",
            requeued_count,
        )


# ── Account identity sync ────────────────────────────────────────────────


def _sync_account_identities(db: Session) -> None:
    """Check if LinkedIn account identities changed in PB.

    For each active LinkedInAccount with a message_agent_id, fetch the
    Message Sender phantom's result.json and extract senderName + senderProfileUrl.
    If the detected name differs from the stored account name, update it and log
    a WARNING so the operator knows the account identity changed.
    """
    from ..db.models import LinkedInAccount
    import httpx as _httpx

    accounts = (
        db.query(LinkedInAccount)
        .filter(
            LinkedInAccount.is_active == True,
            LinkedInAccount.message_agent_id.isnot(None),
        )
        .all()
    )
    if not accounts:
        return

    for acct in accounts:
        try:
            # Get PB client for this account
            pb = _get_pb_client(db, acct.user_id)
            agent_data = pb.get_agent(acct.message_agent_id)
            org_s3 = agent_data.get("orgS3Folder", "")
            agent_s3 = agent_data.get("s3Folder", "")
            pb.close()

            if not org_s3 or not agent_s3:
                continue

            result_url = f"https://phantombuster.s3.amazonaws.com/{org_s3}/{agent_s3}/result.json"
            r = _httpx.get(result_url, timeout=10)
            if r.status_code != 200:
                continue

            rows = r.json()
            if not isinstance(rows, list) or not rows:
                continue

            sender_name = rows[0].get("senderName", "")
            sender_url = rows[0].get("senderProfileUrl", "")
            if not sender_name:
                continue

            # Compare against stored identity
            if acct.name != sender_name:
                old_name = acct.name
                acct.name = sender_name
                acct.linkedin_profile_url = sender_url or acct.linkedin_profile_url
                db.commit()
                logger.warning(
                    "IDENTITY CHANGE: LinkedIn account '%s' → '%s' "
                    "(PB phantom %s now uses a different LinkedIn session)",
                    old_name, sender_name, acct.message_agent_id,
                )
            elif not acct.linkedin_profile_url and sender_url:
                acct.linkedin_profile_url = sender_url
                db.commit()

        except Exception as exc:
            logger.debug("_sync_account_identities: account %s: %s", acct.id, exc)


# ── CSV feed URL ──────────────────────────────────────────────────────────

_CSV_FEED_PATHS = {
    "connect": "/api/pb/connect-leads.csv",
    "message": "/api/pb/message-leads.csv",
}


def _get_csv_feed_url(action_type: str = "connect") -> str:
    """Build the public URL for the PB CSV feed endpoint.

    PB fetches this URL to ingest all pending leads at once.

    Args:
        action_type: ``"connect"`` or ``"message"`` — selects which CSV
                     endpoint to return. The connect CSV has one column
                     (linkedinProfile). The message CSV has two columns
                     (profileUrl, message) with per-lead personalised text.
    """
    path = _CSV_FEED_PATHS.get(action_type, _CSV_FEED_PATHS["connect"])

    # CRITICAL: append webhook secret so the CSV endpoint doesn't return 403.
    # Without this, PB fetches the URL, gets 403, processes 0 leads, and the
    # health check requeues → infinite 0-row loop burning PB execution minutes.
    webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip()
    secret_qs = f"?secret={webhook_secret}" if webhook_secret else ""

    # Prefer an explicit public base URL (e.g. "http://84.247.146.50", the
    # nginx-fronted public endpoint). PhantomBuster fetches this URL from its
    # OWN servers, so it must be the public endpoint on a standard port.
    base = os.getenv("PB_FEED_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}{path}{secret_qs}"

    # Fallback: build from host + port (legacy behaviour).
    host = os.getenv("PUBLIC_HOST", "").strip()
    if not host:
        host = os.getenv("VPS_HOST", "").strip()
    if not host:
        logger.warning(
            "_get_csv_feed_url: none of PB_FEED_BASE_URL, PUBLIC_HOST, VPS_HOST is set"
        )
    scheme = "https" if os.getenv("USE_HTTPS") else "http"
    port = os.getenv("PORT", "3004").strip()
    default_port = "443" if scheme == "https" else "80"
    host_part = host if port in ("", default_port) else f"{host}:{port}"
    return f"{scheme}://{host_part}{path}{secret_qs}"


# ── Drain logic (bulk-feed) ──────────────────────────────────────────────


def _get_user_identity_id(db: Session, user_id: Optional[str]) -> Optional[str]:
    """Get the PB identity_id for a user. Returns None if not set.

    Reads directly from UserCredential rather than _get_user_pb_creds,
    because that helper requires api_key to be present. A user may have
    identity_id without a per-user api_key (sharing the workspace key).
    """
    if not user_id:
        return None
    try:
        from ..db.models import UserCredential
        from ..services.crypto import decrypt
        row = db.query(UserCredential).filter_by(
            user_id=user_id, service="phantombuster", key_name="identity_id"
        ).first()
        if row:
            return decrypt(row.encrypted_value)
    except Exception:
        pass
    return None


# Track last launch time per (user_id, action_type) to avoid flooding PB
_last_launch: dict[str, float] = {}
_LAUNCH_COOLDOWN_SECONDS = 600  # 10 minutes between launches per user+action


def _launch_pending_phantoms(db: Session) -> None:
    """Explicitly launch PB phantoms for users with processing items.

    When a user has an ``identity_id`` configured, we pass it as
    ``bonusArgument.identityId`` so PB uses the correct LinkedIn account.
    This replaces PB's auto-scheduler for multi-user deployments while
    remaining backward-compatible with single-user setups (no identity_id
    = PB uses its default/only session).

    Circuit breaker aware: if the PB circuit breaker is open, logs a
    WARNING and returns early without attempting any launches.
    """
    from ..db.models import LinkedInQueue, LinkedInQueueStatus
    from .phantombuster import CircuitOpenError

    # Circuit breaker early exit: if PB is down, don't waste time
    from .phantombuster import _circuit_open_until
    if _circuit_open_until and _utcnow() < _circuit_open_until:
        logger.warning(
            "_launch_pending_phantoms: PB circuit open until %s, skipping launches",
            _circuit_open_until.isoformat(),
        )
        return

    # Find distinct (user_id, action_type, linkedin_account_id) tuples with processing items.
    # Including linkedin_account_id in the grouping ensures items assigned to different
    # LinkedIn accounts launch separate PB phantoms (multi-account support).
    rows = (
        db.query(LinkedInQueue.user_id, LinkedInQueue.action_type, LinkedInQueue.linkedin_account_id)
        .filter(LinkedInQueue.status == LinkedInQueueStatus.processing)
        .distinct()
        .all()
    )
    if not rows:
        return

    now = time.time()

    for user_id, action_type, linkedin_account_id in rows:
        # Resolve per-user PB client and agent ID first (needed for cooldown key).
        # linkedin_account_id takes priority over user_id credential lookup.
        agent_key = "connect_agent_id" if action_type == "connect" else "message_agent_id"
        env_key = "PHANTOMBUSTER_CONNECT_AGENT_ID" if action_type == "connect" else "PHANTOMBUSTER_MESSAGE_AGENT_ID"
        agent_id = _get_pb_agent_id(db, user_id, agent_key, env_key, linkedin_account_id=linkedin_account_id)
        if not agent_id:
            continue

        # SWAT-M5: cooldown key includes agent_id so different agents don't starve each other
        launch_key = f"{user_id or '_global'}:{agent_id}"
        last = _last_launch.get(launch_key, 0)
        if now - last < _LAUNCH_COOLDOWN_SECONDS:
            continue

        try:
            client = _get_pb_client(db, user_id)
        except Exception as exc:
            logger.warning("_launch_pending_phantoms: no PB client for user %s: %s", user_id, exc)
            continue

        # Build bonusArgument with user-filtered CSV + optional identity
        identity_id = _get_user_identity_id(db, user_id)
        csv_type = "connect" if action_type == "connect" else "message"
        csv_url = _get_csv_feed_url(csv_type)
        if user_id:
            csv_url += f"?user_id={user_id}"

        bonus: dict = {}
        if action_type == "connect":
            bonus["leadsSourceUrl"] = csv_url
        else:
            bonus["spreadsheetUrl"] = csv_url
            # CRITICAL — never send a 2nd message to a lead. reprocessAll=False
            # makes PhantomBuster skip any profile already in its message
            # history, so even if an item lingers in the feed (e.g. the result
            # poller hasn't marked it done yet) the lead cannot be messaged
            # twice. This overrides the phantom's saved reprocessAll=True.
            bonus["reprocessAll"] = False
        if identity_id:
            bonus["identityId"] = identity_id

        # Count how many items we expect the phantom to process so the
        # health-check can flag a 0-row result as suspicious.
        # Filter includes linkedin_account_id to scope to the correct account group.
        _count_q = (
            db.query(LinkedInQueue)
            .filter(
                LinkedInQueue.status == LinkedInQueueStatus.processing,
                LinkedInQueue.action_type == action_type,
                LinkedInQueue.user_id == user_id if user_id else LinkedInQueue.user_id.is_(None),
            )
        )
        if linkedin_account_id:
            _count_q = _count_q.filter(LinkedInQueue.linkedin_account_id == linkedin_account_id)
        else:
            _count_q = _count_q.filter(LinkedInQueue.linkedin_account_id.is_(None))
        item_count = _count_q.count()

        try:
            result = client.launch_agent(agent_id, bonus_argument=bonus)
            _last_launch[launch_key] = now
            container_id = result.get("containerId", "?")
            if container_id and container_id != "?":
                # SWAT-C4: register with (user_id, agent_id) key for scoped health checks
                _launched_containers[(user_id, agent_id)] = (container_id, now, item_count)
            logger.info(
                "Launched %s phantom for user=%s identity=%s container=%s items=%d",
                action_type, user_id or "global",
                identity_id or "default", container_id, item_count,
            )
        except CircuitOpenError:
            # PB circuit breaker tripped mid-loop — stop all launches this cycle
            logger.warning(
                "_launch_pending_phantoms: PB circuit open, skipping remaining launches",
            )
            return
        except Exception as exc:
            # PB may return 429 if phantom is already running — log at WARNING
            # so silent failures are visible (previously INFO, which hid them).
            logger.warning(
                "_launch_pending_phantoms: %s launch for user=%s failed: %s",
                action_type, user_id, exc,
            )


def _drain_once(db: Session) -> None:
    """Validate queued items and transition them from "queued" to "processing".

    This is the first step in the linkedin_queue lifecycle:
      queued → processing → done/failed

    "Processing" items become visible in the CSV feed endpoints
    (/api/pb/connect-leads.csv, /api/pb/message-leads.csv) which PB fetches
    on each phantom launch. Items are validated before promotion:
      - Must have a resolvable user_id (multi-tenant requirement)
      - Message items must have non-empty message_text
      - Items that fail validation are marked "failed" immediately

    After this function runs, _launch_pending_phantoms() explicitly triggers
    PB phantom launches per-user with the correct identityId.

    Connect items also get saved to PB's org-storage (the Connect phantom
    reads from org-storage, not our CSV; the Message phantom reads our CSV).
    """
    from ..db.models import LinkedInQueue, LinkedInQueueStatus

    queued = (
        db.query(LinkedInQueue)
        .filter(LinkedInQueue.status == LinkedInQueueStatus.queued)
        .order_by(LinkedInQueue.created_at)
        .limit(5000)
        .all()
    )
    if not queued:
        return

    now = _utcnow()

    # Resolve user_id for each item; reject unowned and invalid items
    for q in queued:
        uid = _resolve_user_id(db, q)
        if uid is None:
            q.status = LinkedInQueueStatus.failed
            q.result_detail = "unowned: no user_id resolvable"
            q.processed_at = now
            # Audit 2026-05-05 #3: pause orch state so the lead doesn't
            # stay stuck in "waiting for LinkedIn" forever.  Uses the
            # same helper already called at lines 661/673/690.
            logger.warning(
                "_drain_once: item %s has no resolvable user_id (prospect=%s) — "
                "failing and pausing orch state",
                q.id, q.prospect_id,
            )
            _pause_orch_for_failed_linkedin(db, q.prospect_id, q.action_type or "connect")
            continue
        # Message items must have message text — fail immediately if missing
        if q.action_type == "message" and not (q.message_text or "").strip():
            q.status = LinkedInQueueStatus.failed
            q.result_detail = "no_message_text: cannot send empty LinkedIn message"
            q.processed_at = now
            logger.warning("_drain_once: message item %s has no message_text — failed", q.id)
            _pause_orch_for_failed_linkedin(db, q.prospect_id, "message")
            continue
        # Mark as processing — item now appears in CSV feed for PB to pick up
        q.status = LinkedInQueueStatus.processing
        q.processed_at = now
        if not q.user_id:
            q.user_id = uid
    try:
        db.commit()
    except Exception:
        logger.error("_drain_once: commit failed — items reverted to queued")
        db.rollback()
        return

    processing_count = (
        db.query(LinkedInQueue)
        .filter(LinkedInQueue.status == LinkedInQueueStatus.processing)
        .count()
    )

    new_processing = [q for q in queued if q.status == LinkedInQueueStatus.processing]
    logger.info(
        "linkedin_worker: marked %d new items as processing (%d total in CSV feed)",
        len(new_processing),
        processing_count,
    )

    # Connect items need to be saved to PB's org-storage because the
    # Connect phantom ("Outbound Agent LinkedIn Outreach") reads from
    # PB's org-storage list, not our CSV endpoint. Message items don't
    # need this — the Message Sender phantom reads our CSV directly.
    connect_items = [q for q in new_processing if q.action_type == "connect"]
    if connect_items:
        _save_connects_to_pb_org_storage(db, connect_items)


def _resolve_user_id(db: Session, queue_item) -> Optional[str]:
    """Return the user_id that owns this queue item.

    Resolution order:
      1. The queue row's own ``user_id`` column (populated by P3-1 backfill
         and maintained by :func:`queue_linkedin_action`).
      2. The owning Campaign's user_id (queue items launched from a
         campaign sequence step).
      3. The Prospect's user_id (queue items added outside a campaign,
         e.g. from a manual import).

    P3-7: after the P3-1 backfill, every queue row should resolve to a
    real user. A ``None`` return value is treated as an ERROR upstream:
    the worker logs the offending item and SKIPS it rather than
    launching a Phantom under ``<legacy>``  -  this prevents cross-rep
    message bleed on any row that somehow escaped backfill.
    """
    from ..db.models import Campaign, Prospect

    # 1. Trust the explicit column first.
    direct = getattr(queue_item, "user_id", None)
    if direct:
        return direct

    # 2. Fall back to the owning campaign.
    if queue_item.campaign_id:
        c = db.query(Campaign.user_id).filter_by(id=queue_item.campaign_id).first()
        if c and c[0]:
            return c[0]
    # 3. Fall back to the owning prospect.
    p = db.query(Prospect.user_id).filter_by(id=queue_item.prospect_id).first()
    if p and p[0]:
        return p[0]
    return None


def _save_connects_to_pb_org_storage(db: Session, connect_items: list) -> None:
    """Save connect queue items to PB's org-storage so the Connect phantom sees them.

    The Connect phantom ("Outbound Agent LinkedIn Outreach") reads leads from
    PB's org-storage list, not our CSV. New connect queue items must be saved
    to org-storage or they'll never be processed.

    Message items do NOT need this — the Message Sender phantom reads our
    CSV endpoint directly.
    """
    from ..db.models import LinkedInQueueStatus

    if not connect_items:
        return

    # Use the first item's user_id to resolve PB client
    user_id = connect_items[0].user_id
    try:
        client = _get_pb_client(db, user_id)
    except Exception as exc:
        logger.warning("_save_connects_to_pb_org_storage: cannot create PB client: %s", exc)
        return

    leads = []
    for q in connect_items:
        url = (q.linkedin_url or "").strip()
        if url.startswith("http://"):
            url = "https://" + url[7:]
        if url:
            leads.append({"linkedinProfileUrl": url})

    if not leads:
        return

    # Resolve which PB list to save into. The Connect phantom reads from
    # a specific org-storage list configured via its leadsSourceUrl. We
    # must save leads to that same list or PB will never pick them up.
    connect_agent_id = os.getenv("PHANTOMBUSTER_CONNECT_AGENT_ID", "")
    list_id = None
    try:
        agent_info = client._get(f"/agents/fetch?id={connect_agent_id}")
        arg = agent_info.get("argument", "{}")
        import json as _json
        parsed_arg = _json.loads(arg) if isinstance(arg, str) else arg
        source_url = parsed_arg.get("leadsSourceUrl", "")
        # Extract list ID from "org-storage://leads/by-list/XXXXX"
        if "by-list/" in source_url:
            list_id = source_url.split("by-list/")[-1].strip()
    except Exception as exc:
        logger.warning("_save_connects_to_pb_org_storage: could not resolve list ID from agent: %s", exc)

    try:
        # PB save-many has a maxItems limit of 20 per call
        batches = [leads[i:i + 20] for i in range(0, len(leads), 20)]
        saved = 0
        for batch in batches:
            payload = {"leads": batch}
            if list_id:
                payload["listId"] = list_id
            client._post("/org-storage/leads/save-many", payload)
            saved += len(batch)
        logger.info(
            "_save_connects_to_pb_org_storage: saved %d leads to PB org-storage (list=%s)",
            saved, list_id or "default",
        )
    except Exception as exc:
        logger.error(
            "_save_connects_to_pb_org_storage: save failed — rolling %d items back to queued: %s",
            len(connect_items), exc,
        )
        # Roll back to queued so the next cycle retries
        for q in connect_items:
            q.status = LinkedInQueueStatus.queued
            q.processed_at = None
        db.commit()
    finally:
        client.close()


def _get_user_pb_creds(db: Session, user_id: Optional[str]) -> dict | None:
    """Fetch decrypted PB credentials from UserCredential table for a user.

    Returns dict with keys like 'api_key', 'connect_agent_id', etc.
    Returns None if the user has no PB credentials configured.
    """
    if not user_id:
        return None
    try:
        from ..api.credentials import get_user_pb_credentials
        return get_user_pb_credentials(db, user_id)
    except Exception:
        return None


def _get_pb_client(db: Session, user_id: Optional[str] = None):
    """Resolve PhantomBuster API key per user, with global fallback.

    Resolution order:
      1. Per-user UserCredential table (encrypted)
      2. Legacy per-user app_setting
      3. Global env ``PHANTOMBUSTER_API_KEY``
    """
    from .phantombuster import PhantomBusterClient

    # 1. UserCredential table (new)
    if user_id:
        creds = _get_user_pb_creds(db, user_id)
        if creds and creds.get("api_key"):
            return PhantomBusterClient(api_key=creds["api_key"])

    # 2. Legacy app_setting fallback
    if user_id:
        try:
            from ..api.settings import _get_user_setting
            user_key = _get_user_setting(db, "phantombuster_api_key", user_id)
            if user_key:
                return PhantomBusterClient(api_key=user_key)
        except Exception:
            pass

    # 3. Global env fallback
    return PhantomBusterClient()


def _get_pb_agent_id(
    db: Session,
    user_id: Optional[str],
    key_name: str,
    env_fallback: str,
    linkedin_account_id: Optional[str] = None,
) -> str:
    """Resolve a PB agent ID with the following priority chain:
      1. LinkedInAccount (if linkedin_account_id is set on the queue item)
      2. User-level PB credentials (user_id -> user_credentials table)
      3. Global env var fallback (PHANTOMBUSTER_CONNECT_AGENT_ID / PHANTOMBUSTER_MESSAGE_AGENT_ID)
    """
    # Priority 1: LinkedInAccount — explicit per-campaign account assignment
    if linkedin_account_id:
        from ..db.models import LinkedInAccount
        acct = db.query(LinkedInAccount).filter_by(id=linkedin_account_id, is_active=True).first()
        if acct:
            val = getattr(acct, key_name, None)
            if val:
                return val
    # Priority 2: User-level PB credentials
    if user_id:
        creds = _get_user_pb_creds(db, user_id)
        if creds and creds.get(key_name):
            return creds[key_name]
    # Priority 3: Global env var fallback
    return os.getenv(env_fallback, "").strip()


    # NOTE: No phantom launches here. PB's auto-launch scheduler is
    # the sole executor. Our system maintains the CSV feeds; PB reads
    # them on its own schedule and sends connects/messages at its pace.


# ── Async result poller (Phase 3 replacement for synchronous poll) ────────


def _rows_from_container(client, cid: str) -> list[dict]:
    """Extract profile rows from a single PB container's resultObject.

    Handles both the inline-list result (Message Sender writes a per-run list)
    and the ``{"jsonUrl": ...}`` S3 indirection used by the Auto Connect
    phantom's cumulative result.
    """
    import json as _json

    try:
        result_data = client.get_container_result(cid)
    except Exception:
        return []
    if not isinstance(result_data, dict):
        return []
    result_obj = result_data.get("resultObject")
    if isinstance(result_obj, str):
        try:
            result_obj = _json.loads(result_obj)
        except Exception:
            return []
    if isinstance(result_obj, list):
        return [r for r in result_obj if isinstance(r, dict)]
    if isinstance(result_obj, dict):
        json_url = result_obj.get("jsonUrl") or result_obj.get("jsonURL")
        if json_url:
            try:
                import requests as _req
                resp = _req.get(json_url, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    return [r for r in data if isinstance(r, dict)]
            except Exception as exc:
                logger.warning(
                    "_rows_from_container: failed to fetch S3 result JSON: %s", exc,
                )
    return []


def _fetch_agent_cumulative_rows(client, agent_id: str, max_finished: int = 8) -> list[dict]:
    """Fetch processed-profile rows for an agent across its recent runs.

    The Auto Connect phantom accumulates ALL processed profiles in a single
    cumulative resultObject, so one finished container is enough. The Message
    Sender phantom, however, writes PER-RUN results (~one batch per container)
    AND is almost always re-launched ("running"), so its newest container has a
    null resultObject. The previous implementation read only the latest
    container and returned [] whenever the agent was running — which left every
    message-queue item unconfirmed forever.

    Fix: read the most recent FINISHED containers (skip the running one) and
    aggregate their rows. A single large result (>30 rows) is the Auto Connect
    cumulative format, so we early-out there to stay cheap for connects.
    """
    container_ids: list[str] = []
    try:
        conts = client.list_containers(agent_id)
        if isinstance(conts, dict):
            conts = conts.get("containers", conts.get("data", []))
        finished = [
            c for c in conts
            if isinstance(c, dict)
            and (c.get("status") == "finished" or c.get("endType") == "finished")
        ]
        finished.sort(key=lambda c: c.get("endedAt") or c.get("launchedAt") or 0, reverse=True)
        container_ids = [c.get("id") for c in finished[:max_finished] if c.get("id")]
    except Exception as exc:
        logger.warning("_fetch_agent_cumulative_rows: list_containers failed: %s", exc)

    # Fallback: if listing failed, use the latest container only when it is
    # not currently running (a running container's resultObject is null).
    if not container_ids:
        agent_output = client.get_agent_output(agent_id)
        if agent_output.get("status", "") != "running":
            cid = agent_output.get("containerId", "")
            if cid:
                container_ids = [cid]

    aggregated: list[dict] = []
    for cid in container_ids:
        rows = _rows_from_container(client, cid)
        if not rows:
            continue
        aggregated.extend(rows)
        if len(rows) > 30:  # Auto Connect cumulative result — one is enough.
            break
        if len(aggregated) >= 400:
            break
    return aggregated


def _fetch_processing_items_by_user(db: Session) -> dict[str, list]:
    """Fetch all processing LinkedIn queue items and group them by user_id.

    Returns a dict mapping user_key ('_global' or actual user_id) to the
    list of LinkedInQueue ORM objects in 'processing' status. Returns an
    empty dict if no items are pending.
    """
    from ..db.models import LinkedInQueue, LinkedInQueueStatus

    pending = (
        db.query(LinkedInQueue)
        .filter(LinkedInQueue.status == LinkedInQueueStatus.processing)
        .limit(5000)
        .all()
    )
    if not pending:
        return {}

    by_user: dict[str, list] = {}
    for q in pending:
        uid = q.user_id or "_global"
        by_user.setdefault(uid, []).append(q)
    return by_user


def _group_items_by_agent(
    db: Session,
    user_id: Optional[str],
    user_items: list,
) -> dict[tuple[str, str], list]:
    """Group a user's queue items by (agent_id, action_type).

    Resolves the PB agent ID for each item's action_type and groups them.
    Items whose agent ID cannot be resolved are silently dropped.
    linkedin_account_id on each queue item is passed through to _get_pb_agent_id
    so that per-campaign account assignments take priority over user/env defaults.
    """
    by_agent: dict[tuple[str, str], list] = {}
    for q in user_items:
        atype = q.action_type or "connect"
        # Pass linkedin_account_id so per-campaign account takes priority
        _li_acct_id = getattr(q, "linkedin_account_id", None)
        if atype == "connect":
            aid = _get_pb_agent_id(db, user_id, "connect_agent_id", "PHANTOMBUSTER_CONNECT_AGENT_ID", linkedin_account_id=_li_acct_id)
        else:
            aid = _get_pb_agent_id(db, user_id, "message_agent_id", "PHANTOMBUSTER_MESSAGE_AGENT_ID", linkedin_account_id=_li_acct_id)
        if aid:
            by_agent.setdefault((aid, atype), []).append(q)
    return by_agent


def _build_url_index(rows: list[dict]) -> dict[str, dict]:
    """Build a normalized LinkedIn URL to PB result row lookup index.

    Indexes ALL URL fields from each PB row (profileUrl, linkedinProfileUrl,
    url) so LinkedIn redirect variations don't cause missed matches.
    """
    from .linkedin_batch import normalize_linkedin_url

    url_to_row: dict[str, dict] = {}
    for row in rows:
        for field in ("profileUrl", "linkedinProfileUrl", "url"):
            raw = row.get(field, "")
            if raw:
                norm = normalize_linkedin_url(raw)
                if norm:
                    url_to_row[norm] = row
    return url_to_row


def poll_pb_results(db: Session) -> int:
    """Check PB agent cumulative results and reconcile with our queue items.

    This is the result-confirmation step of the linkedin_queue lifecycle.
    For each user's processing items, it:

      1. Fetches PB's cumulative result rows (across recent finished containers)
      2. Builds a normalised-URL index of all PB result rows
      3. Matches each processing queue item against PB results by LinkedIn URL
      4. Classifies each match using classify_phantom_row() → success/neutral/
         pending/failed/unknown
      5. Updates queue item status accordingly (done/failed/keep processing)
      6. Handles timeouts: items not in PB results after 48h (connect) or 24h
         (message) get one auto-retry, then permanently fail

    MATCHING: PB result rows contain LinkedIn URLs that may differ from ours
    in scheme (http vs https), www prefix, and trailing slash. Both sides are
    normalised via normalize_linkedin_url() before comparison.

    TIMEOUT LOGIC: PB processes ~20 profiles/day per phantom. For large queues,
    the timeout is extended dynamically (+24h per 20 items, capped at +5 days)
    so batch launches don't false-timeout.
    """
    from ..db.models import LinkedInQueue, LinkedInQueueStatus
    from .linkedin_batch import normalize_linkedin_url, classify_phantom_row

    # ── Phase 1: Fetch processing items, group by user ───────────────────────
    by_user = _fetch_processing_items_by_user(db)
    if not by_user:
        return 0

    now = _utcnow()
    updated = 0
    dirty = False

    # ── Phase 2-4: Per-user, per-agent processing ────────────────────────────
    for user_key, user_items in by_user.items():
        if _stop_event.is_set():
            break
        user_id = None if user_key == "_global" else user_key
        try:
            client = _get_pb_client(db, user_id)
        except Exception as exc:
            logger.error(
                "poll_pb_results: cannot create PB client for user=%s: %s",
                user_id, exc,
            )
            continue

        by_agent = _group_items_by_agent(db, user_id, user_items)

        for (agent_id, _action_type), agent_items in by_agent.items():
            if _stop_event.is_set():
                break

            try:
                rows = _fetch_agent_cumulative_rows(client, agent_id)
            except Exception as exc:
                logger.warning("poll_pb_results: fetch error: %s", exc)
                rows = []

            url_to_row = _build_url_index(rows)

            not_in_result = 0
            in_pending = 0
            for q in agent_items:
                norm_url = normalize_linkedin_url(q.linkedin_url or "")
                matched_row = url_to_row.get(norm_url)

                if matched_row:
                    verdict, detail = classify_phantom_row(matched_row, q.action_type)
                    if verdict == "success":
                        q.status = LinkedInQueueStatus.done
                        q.result_detail = f"confirmed: {detail}"
                        updated += 1
                        _update_prospect_linkedin_sent(db, q.prospect_id, now)
                        if q.action_type == "message":
                            _set_linkedin_status_by_id(db, q.prospect_id, "messaged")
                    elif verdict == "neutral":
                        q.status = LinkedInQueueStatus.done
                        q.result_detail = f"neutral: {detail}"
                        updated += 1
                        if q.action_type == "message":
                            _set_linkedin_status_by_id(db, q.prospect_id, "messaged")
                    elif verdict == "pending":
                        q.result_detail = f"pending: {detail}"
                        in_pending += 1
                        dirty = True
                    elif verdict == "failed":
                        q.status = LinkedInQueueStatus.failed
                        q.result_detail = f"failed: {detail}"
                        updated += 1
                        _pause_orch_for_failed_linkedin(db, q.prospect_id, q.action_type)
                    else:
                        # Unknown verdict: PB returned a row but we can't
                        # parse it. Keep in processing with a note — only
                        # fail after 72h (configurable via _check_stuck_processing).
                        # This is more lenient than the old 24h timeout because
                        # PB result schemas change and unknown != failed.
                        _ts = q.processed_at or q.created_at
                        if _ts and _ts.tzinfo is None:
                            _ts = _ts.replace(tzinfo=timezone.utc)
                        _age = (now - _ts).total_seconds() if _ts else 0
                        if _age > 259200:  # 72h — let _check_stuck_processing handle it
                            q.status = LinkedInQueueStatus.failed
                            q.result_detail = f"unknown_timeout_72h: {detail}"
                            updated += 1
                            _pause_orch_for_failed_linkedin(db, q.prospect_id, q.action_type)
                            logger.warning(
                                "poll_pb_results: unknown verdict timed out after 72h for %s: %s",
                                q.prospect_id, detail,
                            )
                        else:
                            # Keep in processing — awaiting classification
                            q.result_detail = f"unknown: awaiting classification — {detail}"
                            dirty = True
                else:
                    ts = q.processed_at or q.created_at
                    if ts and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    age = (now - ts).total_seconds() if ts else 0
                    # Dynamic timeout: PB processes ~20 profiles/day per
                    # phantom run. Base timeouts are configurable via config.py
                    # (LINKEDIN_CONNECT_TIMEOUT_HOURS, LINKEDIN_MESSAGE_TIMEOUT_HOURS).
                    # Add 24h per 20 items in this agent batch so large queues
                    # don't false-timeout.
                    batch_size = len(agent_items)
                    # SWAT-H6: cap extra_days at 5 to prevent unbounded timeouts
                    extra_days = min(max(0, (batch_size - 1)) // 20, 5)
                    if q.action_type == "connect":
                        timeout = (LINKEDIN_CONNECT_TIMEOUT_HOURS * 3600) + (extra_days * 86400)
                    else:
                        timeout = (LINKEDIN_MESSAGE_TIMEOUT_HOURS * 3600) + (extra_days * 86400)
                    if age > timeout:
                        # Auto-retry: re-queue up to LINKEDIN_MAX_AUTO_RETRIES
                        # times before giving up. Many timeouts are false
                        # negatives — PB's pacer hadn't reached this profile
                        # yet, or the phantom was launched before the item was
                        # in the CSV feed. Both connects AND messages get retries.
                        retry_count = _get_retry_count(q)
                        if retry_count < LINKEDIN_MAX_AUTO_RETRIES:
                            next_retry = retry_count + 1
                            q.status = LinkedInQueueStatus.queued
                            q.processed_at = None
                            q.result_detail = f"auto_retry_{next_retry} (was no_result_after_{timeout // 3600}h)"
                            updated += 1
                            logger.warning(
                                "poll_pb_results: auto-retrying %s for %s (attempt %d/%d) "
                                "— phantom may have run before this item was in the CSV",
                                q.action_type, q.prospect_id, next_retry,
                                LINKEDIN_MAX_AUTO_RETRIES,
                            )
                        else:
                            q.status = LinkedInQueueStatus.failed
                            q.result_detail = f"no_result_after_{timeout // 3600}h"
                            updated += 1
                            _pause_orch_for_failed_linkedin(db, q.prospect_id, q.action_type)
                            logger.error(
                                "poll_pb_results: %s permanently failed for prospect %s "
                                "after %dh (%d retries exhausted) — PhantomBuster never "
                                "confirmed result. Check PB phantom logs and CSV feed health.",
                                q.action_type, q.prospect_id, timeout // 3600,
                                LINKEDIN_MAX_AUTO_RETRIES,
                            )
                    else:
                        q.result_detail = f"not_in_pb_result (age={int(age)}s)"
                        not_in_result += 1
                        dirty = True

            # PB's auto-launch scheduler is the sole executor. We do NOT
            # launch phantoms from here. Items that are not_in_pb_result
            # will be picked up on PB's next scheduled run (it reads the
            # CSV feed which includes all processing items).
            if updated or in_pending or not_in_result:
                logger.info(
                    "poll_pb_results: %d confirmed, %d pending, %d not_in_result",
                    updated, in_pending, not_in_result,
                )

    if updated or dirty:
        try:
            db.commit()
        except Exception:
            db.rollback()

    return updated


def _maybe_queue_retroactive_message(db: Session, prospect, user_id: Optional[str]) -> None:
    """Queue a LinkedIn message if the sequence already skipped past it.

    Called right after a connection acceptance is detected. Two paths:

    1. Orch state exists: checks if the message step was already passed
       but not completed, then queues retroactively.
    2. No orch state (fallback): queues the message directly from
       EmailSequence if one exists and no message was already sent.

    Dedup: checks linkedin_queue for ANY existing message entry (queued,
    processing, done, or failed) to prevent double-sending.
    """
    try:
        from ..orchestration.orm import OrchSequenceStateORM
        from ..orchestration.config import get_sequence_config
        from ..orchestration.enums import StepType
        from ..db.models import EmailSequence, LinkedInQueue
        from .linkedin_batch import queue_linkedin_action_no_commit
        import json as _json

        state = db.query(OrchSequenceStateORM).filter_by(lead_id=prospect.id).first()

        if state:
            # ── Path 1: orch state exists — check step progression ──
            try:
                config = get_sequence_config(state.sequence_id)
            except KeyError:
                return  # unknown sequence config — skip silently

            msg_step_idx = None
            msg_step = None
            for i, step in enumerate(config.steps):
                if step.step_type == StepType.send_linkedin_message:
                    msg_step_idx = i
                    msg_step = step
                    break

            if msg_step_idx is None:
                return  # no message step in this sequence

            if state.current_step_index <= msg_step_idx:
                return  # haven't reached it yet — normal scheduler handles it

            completed = _json.loads(state.completed_steps) if isinstance(state.completed_steps, str) else (state.completed_steps or [])
            if msg_step.step_id in completed:
                return  # already sent via orch — nothing to do

        # ── Common: get message text and queue ──
        seq = db.query(EmailSequence).filter_by(prospect_id=prospect.id).first()
        message_text = seq.linkedin_message if seq else None
        if not message_text:
            logger.debug(
                "Retroactive message: no linkedin_message text for %s %s — skipping",
                prospect.first_name, prospect.last_name,
            )
            return

        campaign_id = seq.campaign_id if seq else None

        # Dedup: check if ANY message entry already exists (any status)
        existing_msg = (
            db.query(LinkedInQueue)
            .filter_by(
                prospect_id=prospect.id,
                campaign_id=campaign_id,
                action_type="message",
            )
            .first()
        )
        if existing_msg:
            logger.debug(
                "Retroactive message: already exists for %s %s (status=%s) — skipping",
                prospect.first_name, prospect.last_name, existing_msg.status,
            )
            return

        # Resolve linkedin_account_id from the campaign's outreach account
        # so retroactive messages route to the same PB phantom as the original connect.
        _retro_li_acct_id = None
        if campaign_id:
            from ..db.models import Campaign as _Camp
            _retro_camp = db.query(_Camp.outreach_account_id).filter_by(id=campaign_id).first()
            if _retro_camp and _retro_camp[0]:
                _retro_li_acct_id = _retro_camp[0]

        queue_linkedin_action_no_commit(
            db=db,
            prospect_id=prospect.id,
            campaign_id=campaign_id,
            action_type="message",
            linkedin_url=prospect.linkedin_url,
            message_text=message_text,
            user_id=user_id,
            linkedin_account_id=_retro_li_acct_id,
        )
        source = "orch step skipped" if state else "no orch state fallback"
        logger.info(
            "Retroactive LinkedIn message queued for %s %s (%s)",
            prospect.first_name, prospect.last_name, source,
        )
    except Exception as exc:
        logger.warning(
            "Failed to queue retroactive LinkedIn message for %s: %s",
            prospect.id, exc,
        )


def check_connection_acceptances(db: Session) -> int:
    """Detect LinkedIn connection acceptances by cross-referencing PB results.

    This is critical for the outreach flow: the LinkedIn message step has a
    condition "linkedin_connected" that blocks until the prospect accepts the
    connection request. Without this checker, the message step would be
    deferred forever even after the prospect accepted.

    HOW IT WORKS:
      1. Finds all prospects with a LinkedIn URL who aren't marked as connected
      2. Groups them by user_id (for per-user PB client resolution)
      3. Fetches PB's cumulative connect result for each user's agent
      4. For each prospect, checks the PB result row for acceptance signals:
         - connectionDegree == "1st" (direct connection)
         - status/connectionStatus contains "Already connected" or "Request accepted"
      5. Sets Prospect.linkedin_connected = True for accepted leads
      6. Queues a retroactive LinkedIn message if the sequence already passed
         the message step (see _maybe_queue_retroactive_message)

    Returns the number of newly detected acceptances.
    """
    from ..db.models import Prospect
    from .linkedin_batch import normalize_linkedin_url

    # Find all prospects with a LinkedIn URL who aren't marked as connected.
    # Don't require connect_sent - some leads were processed by older code
    # that didn't set it, and PB may have already connected them.
    # Order by oldest-pending first so we prioritise long-waiting leads.
    pending = (
        db.query(Prospect)
        .filter(
            Prospect.linkedin_connected == False,  # noqa: E712
            Prospect.linkedin_url.isnot(None),
        )
        .order_by(Prospect.linkedin_connect_sent_at.asc().nullslast())
        .limit(5000)  # Cap per cycle to avoid fetching tens of thousands
        .all()
    )
    if not pending:
        return 0

    logger.info("check_connection_acceptances: checking %d prospects", len(pending))

    # Group pending prospects by user_id so we resolve per-user PB clients + agent IDs
    by_user: dict[str | None, list] = {}
    for p in pending:
        uid = getattr(p, "user_id", None)
        by_user.setdefault(uid, []).append(p)

    accepted = 0
    for user_id, user_prospects in by_user.items():
        if _stop_event.is_set():
            break

        connect_agent_id = _get_pb_agent_id(db, user_id, "connect_agent_id", "PHANTOMBUSTER_CONNECT_AGENT_ID")
        if not connect_agent_id:
            continue

        try:
            client = _get_pb_client(db, user_id)
            rows = _fetch_agent_cumulative_rows(client, connect_agent_id)
        except Exception as exc:
            logger.debug(
                "check_connection_acceptances: fetch failed for user=%s: %s",
                user_id, exc,
            )
            continue

        if not rows:
            continue

        # Build URL → row lookup
        url_to_row: dict[str, dict] = {}
        for row in rows:
            for field in ("profileUrl", "linkedinProfileUrl", "url"):
                raw = row.get(field, "")
                if raw:
                    norm = normalize_linkedin_url(raw)
                    if norm:
                        url_to_row[norm] = row
                        # No break - index ALL URL fields to handle LinkedIn slug redirects

        for prospect in user_prospects:
            norm_url = normalize_linkedin_url(prospect.linkedin_url or "")
            if not norm_url:
                continue
            pb_row = url_to_row.get(norm_url)
            if not pb_row:
                continue

            # Check multiple signals for acceptance
            degree = (pb_row.get("connectionDegree") or "").strip()
            status = (pb_row.get("status") or "").strip().lower()
            conn_status = (pb_row.get("connectionStatus") or "").strip().lower()

            is_accepted = (
                degree == "1st"
                or "already connected" in status
                or "already connected" in conn_status
                or "request accepted" in status
                or "request accepted" in conn_status
            )

            if is_accepted:
                prospect.linkedin_connected = True
                accepted += 1
                logger.info(
                    "check_connection_acceptances: %s %s accepted connection "
                    "(degree=%s, status=%s)",
                    prospect.first_name, prospect.last_name, degree, status,
                )
                # Update prospect + sequence status to 'connected'
                _set_linkedin_status(db, prospect, "connected")
                # Retroactive LinkedIn message: if the sequence already
                # advanced past the message step without sending, queue
                # the message now that the lead is connected.
                _maybe_queue_retroactive_message(db, prospect, user_id)

    if accepted:
        try:
            db.commit()
        except Exception:
            db.rollback()
        logger.info(
            "check_connection_acceptances: %d new acceptances detected "
            "(%d still pending)",
            accepted, len(pending) - accepted,
        )

    return accepted


def _set_linkedin_status(db: Session, prospect, new_status: str) -> None:
    """Update prospect + sequence statuses for LinkedIn lifecycle transitions.

    Only advances forward: enrolled → connected → messaged → replied.
    """
    from ..db.models import (
        EmailSequence, ProspectStatus, SequenceStatus,
    )
    _order = {"pending": 0, "enrolled": 1, "connected": 2, "messaged": 3, "replied": 4}
    new_rank = _order.get(new_status, 0)
    cur_rank = _order.get(prospect.status.value if prospect.status else "pending", 0)

    if new_rank > cur_rank:
        prospect.status = ProspectStatus(new_status)

    target_seq = SequenceStatus(new_status)
    for seq in db.query(EmailSequence).filter_by(prospect_id=prospect.id).all():
        seq_rank = _order.get(seq.status.value if seq.status else "pending", 0)
        if new_rank > seq_rank:
            seq.status = target_seq


def _set_linkedin_status_by_id(db: Session, prospect_id: str, new_status: str) -> None:
    """Same as _set_linkedin_status but looks up prospect by ID."""
    from ..db.models import Prospect
    p = db.query(Prospect).filter_by(id=prospect_id).first()
    if p:
        _set_linkedin_status(db, p, new_status)


def _update_prospect_linkedin_sent(db: Session, prospect_id: str, now: datetime) -> None:
    """Set linkedin_connect_sent_at on the prospect (ground truth for dashboard)."""
    from ..db.models import Prospect
    p = db.query(Prospect).filter_by(id=prospect_id).first()
    if p and not p.linkedin_connect_sent_at:
        p.linkedin_connect_sent_at = now


def _pause_orch_for_failed_linkedin(db: Session, prospect_id: str, action_type: str) -> None:
    """Pause orch state for a confirmed LinkedIn failure."""
    try:
        from .linkedin_batch import _pause_orch_state_for_linkedin_failure
        _pause_orch_state_for_linkedin_failure(
            db, prospect_id, action_type, "failed", "pb_poller_no_confirmation",
        )
    # Bug #9 fix: log the error instead of swallowing
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "_pause_orch_for_failed_linkedin failed for prospect=%s: %s",
            prospect_id, exc,
        )
