"""
Conference reply poller — background daemon that detects inbound replies to
conference outreach and prevents the system from drafting follow-ups on top
of an active conversation.

Part of the conference (Events tab) outreach flow. This module solves a critical
gap: conference emails are drafted via Graph into the user's Outlook, the user
manually sends them, and replies arrive in their inbox. Without this poller,
the system has no visibility into those replies and would happily let the rep
push email 2 and email 3 on top of a prospect who already replied.

How it works:
  Every ~10 minutes (configurable via CONFERENCE_REPLY_POLLER_INTERVAL_SECONDS),
  the poller iterates every user who has at least one drafted-but-unreplied
  conference lead. For each user, it fetches recent inbox messages via Graph,
  matches sender addresses against the user's leads, and marks matches as replied.

Match rules (v1) — a lead is marked replied when ALL of these are true:
  - The lead belongs to the user whose Outlook inbox we're polling
  - draft_created_at IS NOT NULL (we actually drafted outreach to them)
  - replied_at IS NULL (not already marked in a previous poll cycle)
  - An inbox message from lead.email (case-insensitive) exists
  - That message's received_at is strictly AFTER the lead's draft_created_at
    (prevents false positives from unrelated prior emails)
  - The message is NOT a draft (is_draft=False)
  - The sender is NOT the user themselves (skip self-reply loops)

Known limitation: Replies from a different address (e.g. prospect replies from
personal Gmail instead of work email) are NOT caught. False negatives are safer
than false positives — an uncaught reply just means the rep sees the reply in
Outlook and skips the next draft manually.

Side effects of marking a lead as replied:
  - Sets replied_at, reply_subject, reply_snippet, reply_from_email on the lead
  - Cancels all pending Outlook calendar reminders for that lead (email 2, 3, ...)

Public API:
  - start_conference_reply_poller() — call once from main.py lifespan startup
  - stop_conference_reply_poller(timeout) — call once from lifespan shutdown
  - conference_reply_poller_alive() — for the watchdog liveness check
  - poll_replies_for_user(db, user) — synchronous single-user poll, used by
    the POST /conference/check-replies manual trigger endpoint
  - _match_and_mark(db, user, messages) — pure matching logic, exposed for testing
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import ConferenceLead, User

logger = logging.getLogger(__name__)


# ── Module state (single poller per process) ──────────────────────────────
_poller_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()

# Keyed per user_id so one slow Outlook account doesn't replay messages
# for the other users. Updated after each successful per-user poll.
_last_poll_at: dict[str, datetime] = {}

# Polling interval (seconds). 600 = 10 min by default. Configurable so
# a test can crank it way down.
POLLER_INTERVAL_SECONDS = int(
    os.getenv("CONFERENCE_REPLY_POLLER_INTERVAL_SECONDS", "600")
)
# On the very first poll (or after restart) we don't know how far back
# to scan. Default: 24 hours. This gives us decent recovery after a
# server restart without dragging in stale conversations from last month.
POLLER_COLD_START_LOOKBACK_HOURS = int(
    os.getenv("CONFERENCE_REPLY_POLLER_COLD_START_HOURS", "24")
)
# Per-poll safety cap on how many messages we fetch from each user's
# inbox. Prevents a runaway fetch if someone goes weeks between polls.
POLLER_MAX_MESSAGES_PER_POLL = int(
    os.getenv("CONFERENCE_REPLY_POLLER_MAX_MESSAGES", "200")
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Public lifecycle API ──────────────────────────────────────────────────


def conference_reply_poller_alive() -> bool:
    """Return True if the poller thread is currently running."""
    return _poller_thread is not None and _poller_thread.is_alive()


def start_conference_reply_poller() -> None:
    """Start the conference reply poller thread.

    Idempotent: a second call while a poller is alive is a no-op. The
    poller is a daemon thread so the process can exit cleanly even if
    the lifespan shutdown handler is skipped.
    """
    global _poller_thread
    if conference_reply_poller_alive():
        logger.debug("conference_reply_poller: already running, ignoring start request")
        return

    _stop_event.clear()
    _poller_thread = threading.Thread(
        target=_poller_loop, name="conf-reply-poller", daemon=True
    )
    _poller_thread.start()
    logger.info(
        "conference_reply_poller: started (interval=%ds, cold_start_lookback=%dh)",
        POLLER_INTERVAL_SECONDS, POLLER_COLD_START_LOOKBACK_HOURS,
    )


def stop_conference_reply_poller(timeout: float = 30.0) -> None:
    """Signal the poller to stop and wait up to ``timeout`` seconds for it."""
    global _poller_thread
    if not conference_reply_poller_alive():
        return
    _stop_event.set()
    logger.info("conference_reply_poller: stop signal sent")
    _poller_thread.join(timeout=timeout)
    if _poller_thread.is_alive():
        logger.warning(
            "conference_reply_poller: did not drain within %.0fs  -  exiting anyway",
            timeout,
        )
    else:
        logger.info("conference_reply_poller: drained cleanly")
    _poller_thread = None


# ── Poller loop ───────────────────────────────────────────────────────────


def _poller_loop() -> None:
    """Main loop: iterate active users, poll each, sleep, repeat."""
    from ..db.database import SessionLocal

    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            try:
                _poll_all_users(db)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "conference_reply_poller: top-level crash: %s",
                    exc, exc_info=True,
                )
                try:
                    db.rollback()
                except Exception:
                    pass
        finally:
            try:
                db.close()
            except Exception:
                pass

        # Wait until next interval, waking immediately on stop signal.
        if _stop_event.wait(timeout=POLLER_INTERVAL_SECONDS):
            return


def _poll_all_users(db: Session) -> None:
    """Iterate every user who has at least one drafted, unreplied
    conference lead and poll their Outlook inbox for replies.

    Users without a connected Outlook token are skipped silently  -  a
    log line at DEBUG level only so ops noise stays low.
    """
    # Find users with at least one active drafted-but-not-replied lead.
    # A user with no active leads has nothing to match against, so
    # we save a Graph API call by skipping them entirely.
    rows = (
        db.query(User)
        .join(ConferenceLead, ConferenceLead.user_id == User.id)
        .filter(
            ConferenceLead.draft_created_at.isnot(None),
            ConferenceLead.replied_at.is_(None),
        )
        .distinct()
        .all()
    )
    if not rows:
        logger.debug("conference_reply_poller: no users with active drafted leads")
        return

    for user in rows:
        if _stop_event.is_set():
            return
        try:
            marked = poll_replies_for_user(db, user)
            if marked:
                logger.info(
                    "conference_reply_poller: marked %d lead(s) for user %s",
                    marked, user.email,
                )
        except Exception as exc:  # noqa: BLE001
            # One user's Graph failure (expired token, rate limit, etc.)
            # must not stop the others. Swallow per-user and continue.
            logger.warning(
                "conference_reply_poller: user %s failed: %s",
                user.email, exc,
            )


def poll_replies_for_user(db: Session, user: User) -> int:
    """
    Poll a single user's Outlook inbox for replies and mark any matching
    leads. Returns the number of leads newly marked as replied.

    Used by:
      1. The background loop in ``_poll_all_users``.
      2. The ``POST /conference/check-replies`` route for an immediate
         manual refresh from the dashboard.

    Raises on hard failures (token invalid, Graph unreachable) so the
    route endpoint can surface a clear error to the operator. The
    background loop catches the exception per-user.
    """
    from ..api.conference._shared import _outlook_token_file
    from .outlook import OutlookClient

    token_file = _outlook_token_file(user)
    if not token_file.exists():
        logger.debug(
            "conference_reply_poller: user %s has no Outlook token  -  skipping",
            user.email,
        )
        return 0

    # Determine the `since` cursor  -  last successful poll, or cold-start fallback.
    since = _last_poll_at.get(user.id)
    if since is None:
        since = _utcnow() - timedelta(hours=POLLER_COLD_START_LOOKBACK_HOURS)

    # Graph wants a Zulu-format ISO-8601 string for $filter datetime comparisons.
    since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    client = OutlookClient(token_file=token_file)
    try:
        # Ensure we have a valid access token before we try to fetch  -
        # authenticate() handles silent refresh and raises cleanly if
        # the cache is empty/expired.
        client.authenticate()
        messages = client.fetch_inbox_messages(
            since_iso=since_iso,
            top=POLLER_MAX_MESSAGES_PER_POLL,
        )
    finally:
        client.close()

    marked = _match_and_mark(db, user, messages)

    # Only advance the cursor AFTER a successful fetch + mark, so a
    # crashed poll is retried with the same window on the next run.
    _last_poll_at[user.id] = _utcnow()
    return marked


# ── Match logic (pure, unit-testable) ─────────────────────────────────────


def _match_and_mark(
    db: Session,
    user: User,
    messages: list[dict],
    now: Optional[datetime] = None,
) -> int:
    """
    Walk ``messages`` (as returned by ``OutlookClient.fetch_inbox_messages``)
    and mark any matching ConferenceLead rows as replied.

    Pure-ish: takes an already-loaded message list and a DB session,
    returns a count. Commits the session if any rows were updated.
    Exposed with an underscore so tests can exercise the match rules
    without having to mock Graph.

    Args:
        db: SQLAlchemy session bound to the user's DB.
        user: The ``User`` whose leads we're matching against.
        messages: Simplified message dicts with keys
            ``from_email`` (lowercased), ``subject``, ``body_preview``,
            ``received_at`` (ISO 8601 from Graph), ``is_draft``.
        now: Injectable for tests. Defaults to ``datetime.utcnow()``.

    Returns: count of leads newly marked.
    """
    if now is None:
        now = _utcnow()

    if not messages:
        return 0

    user_email = (user.email or "").strip().lower()

    # Load all of this user's active unreplied drafted leads in ONE query
    # so we can do the email match in memory. This is cheaper than a
    # per-message SELECT even for large inboxes.
    active = (
        db.query(ConferenceLead)
        .filter(
            ConferenceLead.user_id == user.id,
            ConferenceLead.draft_created_at.isnot(None),
            ConferenceLead.replied_at.is_(None),
        )
        .all()
    )
    if not active:
        return 0

    # SWAT-H9: map email → list of leads so ALL leads sharing an email get
    # marked when a reply arrives (not just the most-recently-drafted one).
    by_email: dict[str, list[ConferenceLead]] = {}
    for lead in active:
        e = (lead.email or "").strip().lower()
        if not e:
            continue
        by_email.setdefault(e, []).append(lead)

    marked = 0
    for msg in messages:
        # Skip drafts  -  those are our own outbound that haven't been sent.
        if msg.get("is_draft"):
            continue

        from_email = (msg.get("from_email") or "").strip().lower()
        if not from_email:
            continue

        # Skip messages sent BY the user themselves (reply-all loops, etc.)
        if user_email and from_email == user_email:
            continue

        leads = by_email.get(from_email)
        if not leads:
            continue

        received_at = _parse_iso8601(msg.get("received_at"))
        if received_at is None:
            continue

        # Mark every lead whose draft pre-dates this reply.
        matched_any = False
        for lead in leads:
            if lead.replied_at is not None:
                continue  # already marked in this or a previous poll
            if lead.draft_created_at is not None and received_at <= lead.draft_created_at.replace(tzinfo=timezone.utc):
                continue

            lead.replied_at = now.replace(tzinfo=None)
            lead.reply_subject = (msg.get("subject") or "")[:2000]
            lead.reply_snippet = (msg.get("body_preview") or "")[:2000]
            lead.reply_from_email = from_email
            marked += 1
            matched_any = True

            if lead.calendar_event_ids:
                try:
                    from ..api.conference.service import cancel_followup_reminders
                    cancel_followup_reminders(lead, user)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "reply_poller: calendar cancel failed for %s: %s",
                        lead.email, exc,
                    )

        # Remove from the map so two replies from the same prospect in
        # the same poll only count once (idempotent within a single run).
        if matched_any:
            del by_email[from_email]

    if marked:
        db.commit()
    return marked


# ── Helpers ───────────────────────────────────────────────────────────────


def _parse_iso8601(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string returned by Graph. Graph emits
    values like ``2026-04-09T08:04:07.394276Z``. datetime.fromisoformat
    in Python 3.11+ accepts the Z suffix; on earlier versions we strip
    it manually."""
    if not value:
        return None
    try:
        # Python 3.11+ accepts 'Z' directly; earlier versions need a swap.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def clear_last_poll_cursor(user_id: Optional[str] = None) -> None:
    """
    Reset the per-user ``_last_poll_at`` cursor. Used by:
      1. Tests that want a deterministic first-poll window.
      2. The ``POST /conference/check-replies?full_rescan=true`` option
         (not implemented in v1, reserved).

    If ``user_id`` is None, clears the entire cache.
    """
    if user_id is None:
        _last_poll_at.clear()
    else:
        _last_poll_at.pop(user_id, None)
