"""
Inbound lead poller — detects new external contacts emailing sales@invigilo
and registers them as inbound leads in the GTM Analytics dashboard.

Architecture
------------
Follows the same pattern as ``conference_reply_poller.py``: a background
daemon thread polls on a fixed interval, fetches inbox messages via
Microsoft Graph, and writes to the database.

The target inbox defaults to the ``INBOUND_LEAD_POLLER_INBOX`` env var
(``demo-member-011@company.example``). This is expected to be a shared mailbox that
any Outlook-connected user has delegated read access to.

A new external sender is registered as one ``GtmManualEntry`` row per
unique (sender_email, month) pair. Subsequent emails from the same
address in the same month are de-duplicated.

Public API
----------
- ``start_inbound_lead_poller()``  — call once from main.py lifespan startup.
- ``stop_inbound_lead_poller(timeout)``  — call once from lifespan shutdown.
- ``inbound_lead_poller_alive()``  — for the watchdog liveness check.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import GtmManualEntry, User

logger = logging.getLogger(__name__)


# ── Module state ──────────────────────────────────────────────────────────
_poller_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_last_poll_at: Optional[datetime] = None

POLLER_INTERVAL_SECONDS = int(
    os.getenv("INBOUND_LEAD_POLLER_INTERVAL_SECONDS", "1800")
)
POLLER_INBOX = os.getenv("INBOUND_LEAD_POLLER_INBOX", "demo-member-011@company.example")
POLLER_COLD_START_LOOKBACK_HOURS = int(
    os.getenv("INBOUND_LEAD_POLLER_COLD_START_HOURS", "48")
)
POLLER_MAX_MESSAGES = int(
    os.getenv("INBOUND_LEAD_POLLER_MAX_MESSAGES", "200")
)

# Domains to skip (internal). Lowercase.
INTERNAL_DOMAINS = {
    d.strip().lower()
    for d in os.getenv("INBOUND_LEAD_POLLER_INTERNAL_DOMAINS",
                       "invigilo.ai,company.example").split(",")
    if d.strip()
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Public lifecycle ──────────────────────────────────────────────────────

def inbound_lead_poller_alive() -> bool:
    return _poller_thread is not None and _poller_thread.is_alive()


def start_inbound_lead_poller() -> None:
    global _poller_thread
    if inbound_lead_poller_alive():
        logger.debug("inbound_lead_poller: already running")
        return

    _stop_event.clear()
    _poller_thread = threading.Thread(
        target=_poller_loop, name="inbound-lead-poller", daemon=True
    )
    _poller_thread.start()
    logger.info(
        "inbound_lead_poller: started (interval=%ds, inbox=%s)",
        POLLER_INTERVAL_SECONDS, POLLER_INBOX,
    )


def stop_inbound_lead_poller(timeout: float = 15.0) -> None:
    global _poller_thread
    if not inbound_lead_poller_alive():
        return
    _stop_event.set()
    logger.info("inbound_lead_poller: stop signal sent")
    _poller_thread.join(timeout=timeout)
    if _poller_thread.is_alive():
        logger.warning("inbound_lead_poller: did not drain within %.0fs", timeout)
    else:
        logger.info("inbound_lead_poller: drained cleanly")
    _poller_thread = None


# ── Poller loop ───────────────────────────────────────────────────────────

def _poller_loop() -> None:
    from ..db.database import SessionLocal

    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            try:
                _poll_inbox(db)
            except Exception as exc:  # noqa: BLE001
                logger.error("inbound_lead_poller: crash: %s", exc, exc_info=True)
                try:
                    db.rollback()
                except Exception:
                    pass
        finally:
            try:
                db.close()
            except Exception:
                pass

        if _stop_event.wait(timeout=POLLER_INTERVAL_SECONDS):
            return


def _poll_inbox(db: Session) -> None:
    global _last_poll_at

    from .outlook import OutlookClient
    from ..api.conference._shared import _outlook_token_file
    from pathlib import Path

    client = None

    # 1) Prefer the dedicated inbound-sales token (connected via GTM Analytics tab)
    _outreach_dir = Path(__file__).resolve().parent.parent.parent
    dedicated_token = _outreach_dir / "token_cache_inbound-sales.json"
    if dedicated_token.exists():
        try:
            client = OutlookClient(token_file=dedicated_token)
            client.authenticate()
            logger.debug("inbound_lead_poller: using dedicated inbound-sales token")
        except Exception as exc:
            logger.debug("inbound_lead_poller: dedicated token unusable: %s", exc)
            client = None

    # 2) Fall back to any user's personal Outlook token
    if client is None:
        users = db.query(User).all()
        for user in users:
            token_file = _outlook_token_file(user)
            if token_file.exists():
                try:
                    client = OutlookClient(token_file=token_file)
                    client.authenticate()
                    break
                except Exception as exc:
                    logger.debug(
                        "inbound_lead_poller: user %s token unusable: %s",
                        user.email, exc,
                    )
                    client = None

    if client is None:
        logger.debug("inbound_lead_poller: no usable Outlook token found")
        return

    try:
        since = _last_poll_at
        if since is None:
            since = _utcnow() - timedelta(hours=POLLER_COLD_START_LOOKBACK_HOURS)

        since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        messages = client.fetch_inbox_messages(
            since_iso=since_iso,
            top=POLLER_MAX_MESSAGES,
            mailbox_email=POLLER_INBOX,
        )
    finally:
        client.close()

    registered = _register_inbound_leads(db, messages)
    _last_poll_at = _utcnow()

    if registered:
        logger.info("inbound_lead_poller: registered %d new inbound lead(s)", registered)


def _register_inbound_leads(db: Session, messages: list[dict]) -> int:
    """Process fetched messages and register new external senders as inbound leads.

    Exposed for unit testing.
    """
    if not messages:
        return 0

    now = _utcnow()
    registered = 0
    # Track (email, month) pairs seen in this batch to dedup within a single poll
    seen_in_batch: set[tuple[str, str]] = set()

    for msg in messages:
        if msg.get("is_draft"):
            continue

        from_email = (msg.get("from_email") or "").strip().lower()
        if not from_email:
            continue

        # Skip internal domains
        domain = from_email.rsplit("@", 1)[-1] if "@" in from_email else ""
        if domain in INTERNAL_DOMAINS:
            continue

        # Determine month from received_at
        received_at_str = msg.get("received_at", "")
        if received_at_str:
            try:
                if received_at_str.endswith("Z"):
                    received_at_str = received_at_str[:-1] + "+00:00"
                received_dt = datetime.fromisoformat(received_at_str)
                month = received_dt.strftime("%Y-%m")
            except (ValueError, TypeError):
                month = now.strftime("%Y-%m")
        else:
            month = now.strftime("%Y-%m")

        # Dedup within this batch
        batch_key = (from_email, month)
        if batch_key in seen_in_batch:
            continue
        seen_in_batch.add(batch_key)

        # Dedup against DB: check if we already have this sender for this month
        existing = db.query(GtmManualEntry.id).filter(
            GtmManualEntry.category == "inbound",
            GtmManualEntry.metric_key == "inbound_lead",
            GtmManualEntry.source_ref == from_email,
            GtmManualEntry.month == month,
        ).first()

        if existing:
            continue

        entry = GtmManualEntry(
            id=str(uuid.uuid4()),
            user_id=None,
            category="inbound",
            metric_key="inbound_lead",
            month=month,
            value=1,
            note=f"{msg.get('from_name', '')} — {msg.get('subject', '')}".strip(" —"),
            source_ref=from_email,
            created_by=None,
            created_at=now,
            updated_at=now,
        )
        db.add(entry)
        registered += 1

    if registered:
        db.commit()

    return registered
