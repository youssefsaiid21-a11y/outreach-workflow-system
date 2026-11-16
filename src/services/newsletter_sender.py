"""
Newsletter send engine — the core module that actually delivers newsletters.

Part of Example's B2B outreach system. Newsletters are AI-composed content
pieces sent to prospect, client, or HubSpot-synced contact lists via Microsoft
Graph API (Outlook). This module handles:

  1. **Send orchestration**: Iterates recipients, renders per-recipient HTML
     (with personalized unsubscribe links), sends via Graph POST /me/sendMail,
     and tracks per-recipient success/failure in the DB.

  2. **Background scheduler**: A daemon thread checks every 60s for newsletters
     with status=scheduled whose scheduled_at has passed, then auto-sends them.
     Uses compare-and-swap (CAS) to prevent double-sends when the scheduler
     and a manual /send call race.

  3. **HubSpot auto-sync scheduler**: A separate daemon thread periodically
     (default every 7 days) pulls contacts from HubSpot's "To Nurture" deal
     stage and syncs them as newsletter recipients. Only affects newsletters
     with track=hubspot. Adds new contacts, removes stale ones (if still
     pending), and respects the suppression list.

  4. **Suppression sweep**: Before every send, re-checks all pending recipients
     against both newsletter-specific and campaign-level suppression lists.
     This catches anyone who unsubscribed between when they were added as a
     recipient and when the newsletter actually sends.

Relationships:
  - Uses OutlookClient (src/services/outlook.py) for Graph API calls
  - Uses newsletter_templates.py for HTML rendering
  - Uses suppression.py for cross-system unsubscribe checks
  - Called by newsletter API routes (src/api/newsletters.py)
  - DB models: Newsletter, NewsletterRecipient, User (src/db/models.py)
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import (
    Newsletter,
    NewsletterRecipient,
    NewsletterRecipientStatus,
    NewsletterStatus,
    NewsletterTrack,
    User,
)

logger = logging.getLogger(__name__)

# Graph API base URL — must stay in sync with src/services/outlook.py.
# Used here only for the _send_single method's direct Graph call.
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Thread-safety lock for newsletter status transitions. Prevents races between:
#   - The background scheduler thread picking up a due newsletter
#   - A user clicking "Send Now" on the same newsletter from the dashboard
# On PostgreSQL, the CAS query handles this at the DB level; the lock is
# the fallback for SQLite (single-process tests).
_status_lock = threading.Lock()

# ── Background scheduler ────────────────────────────────────────────────────

_newsletter_stop = threading.Event()
_newsletter_thread: Optional[threading.Thread] = None

# How often (seconds) the scheduler checks for due newsletters.
_SCHEDULER_CHECK_INTERVAL = 60


def start_newsletter_scheduler() -> threading.Thread:
    """Start the background thread that checks for due newsletters.

    Returns:
        The started daemon thread.
    """
    global _newsletter_thread
    _newsletter_stop.clear()
    thread = threading.Thread(
        target=_newsletter_scheduler_loop,
        name="newsletter-scheduler",
        daemon=True,
    )
    thread.start()
    _newsletter_thread = thread
    logger.info("Newsletter scheduler started (interval=%ds).", _SCHEDULER_CHECK_INTERVAL)
    return thread


def stop_newsletter_scheduler(timeout: float = 15) -> None:
    """Signal the newsletter scheduler to stop and wait for it to drain.

    Args:
        timeout: Max seconds to wait for thread join.
    """
    global _newsletter_thread
    _newsletter_stop.set()
    if _newsletter_thread is not None and _newsletter_thread.is_alive():
        _newsletter_thread.join(timeout=timeout)
        if _newsletter_thread.is_alive():
            logger.warning("Newsletter scheduler thread did not drain within %ds.", timeout)
        else:
            logger.info("Newsletter scheduler thread drained cleanly.")
    _newsletter_thread = None


def _newsletter_scheduler_loop() -> None:
    """Background loop: every interval, check for scheduled newsletters that are due."""
    while not _newsletter_stop.is_set():
        try:
            _check_and_send_due_newsletters()
        except Exception as exc:
            logger.error("Newsletter scheduler loop error: %s", exc, exc_info=True)
        _newsletter_stop.wait(_SCHEDULER_CHECK_INTERVAL)


def _check_and_send_due_newsletters() -> None:
    """Query for newsletters with status=scheduled and scheduled_at <= now, send each."""
    from ..db.database import SessionLocal

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due = (
            db.query(Newsletter)
            .filter(
                Newsletter.status == NewsletterStatus.scheduled,
                Newsletter.scheduled_at <= now,
            )
            .all()
        )
        for nl in due:
            if _newsletter_stop.is_set():
                return
            logger.info(
                "Newsletter scheduler: sending due newsletter '%s' (id=%s, scheduled_at=%s).",
                nl.title, nl.id, nl.scheduled_at,
            )
            try:
                service = NewsletterSendService()
                service.send_newsletter(db, nl.id)
            except Exception as exc:
                logger.error(
                    "Newsletter scheduler: failed to send '%s' (id=%s): %s",
                    nl.title, nl.id, exc, exc_info=True,
                )
    finally:
        db.close()


# ── HubSpot auto-sync scheduler ─────────────────────────────────────────────

_hubspot_sync_stop = threading.Event()
_hubspot_sync_thread: Optional[threading.Thread] = None

# Default: sync every 7 days (604800s). Override via env var for more/less frequent syncs.
# The HubSpot sync pulls contacts from the "To Nurture" deal stage and adds them
# as recipients to any hubspot-track newsletter that is still in draft/scheduled state.
_HUBSPOT_SYNC_INTERVAL = int(os.getenv("HUBSPOT_NEWSLETTER_SYNC_INTERVAL_SECONDS", "604800"))


def start_hubspot_sync_scheduler() -> threading.Thread:
    """Start background thread that auto-syncs HubSpot newsletter recipients."""
    global _hubspot_sync_thread
    _hubspot_sync_stop.clear()
    thread = threading.Thread(
        target=_hubspot_sync_loop,
        name="hubspot-newsletter-sync",
        daemon=True,
    )
    thread.start()
    _hubspot_sync_thread = thread
    logger.info(
        "HubSpot newsletter sync scheduler started (interval=%ds / %.1f days).",
        _HUBSPOT_SYNC_INTERVAL, _HUBSPOT_SYNC_INTERVAL / 86400,
    )
    return thread


def stop_hubspot_sync_scheduler(timeout: float = 15) -> None:
    """Signal the HubSpot sync scheduler to stop."""
    global _hubspot_sync_thread
    _hubspot_sync_stop.set()
    if _hubspot_sync_thread is not None and _hubspot_sync_thread.is_alive():
        _hubspot_sync_thread.join(timeout=timeout)
    _hubspot_sync_thread = None


def _hubspot_sync_loop() -> None:
    """Background loop: periodically sync HubSpot newsletter recipients."""
    # Wait a short time on startup before first check
    _hubspot_sync_stop.wait(60)
    while not _hubspot_sync_stop.is_set():
        try:
            _sync_all_hubspot_newsletters()
        except Exception as exc:
            logger.error("HubSpot sync loop error: %s", exc, exc_info=True)
        _hubspot_sync_stop.wait(_HUBSPOT_SYNC_INTERVAL)


def _sync_all_hubspot_newsletters() -> None:
    """Find all hubspot-track newsletters that aren't currently sending, and sync them."""
    from ..db.database import SessionLocal

    db = SessionLocal()
    try:
        newsletters = (
            db.query(Newsletter)
            .filter(
                Newsletter.track == NewsletterTrack.hubspot,
                Newsletter.status.in_([NewsletterStatus.draft, NewsletterStatus.scheduled]),
            )
            .all()
        )
        if not newsletters:
            return

        logger.info("HubSpot auto-sync: found %d hubspot newsletter(s) to sync.", len(newsletters))

        from ..services.hubspot import HubSpotClient
        try:
            client = HubSpotClient()
        except ValueError as exc:
            logger.error("HubSpot auto-sync: %s", exc)
            return

        try:
            hubspot_contacts = client.list_nurture_contacts()
        except Exception as exc:
            logger.error("HubSpot auto-sync: failed to fetch contacts: %s", exc)
            return
        finally:
            client.close()

        hs_emails = {}
        for c in hubspot_contacts:
            email = c["email"].strip().lower()
            if email:
                hs_emails[email] = c

        for nl in newsletters:
            if _hubspot_sync_stop.is_set():
                return
            try:
                _sync_one_newsletter(db, nl, hs_emails)
            except Exception as exc:
                logger.error(
                    "HubSpot auto-sync: failed for newsletter '%s' (id=%s): %s",
                    nl.title, nl.id, exc,
                )
    finally:
        db.close()


def _sync_one_newsletter(db, nl, hs_emails: dict) -> None:
    """Sync a single hubspot newsletter's recipients against the HubSpot contact set.

    Bidirectional sync logic:
      - Adds contacts present in HubSpot but missing from the newsletter (unless suppressed)
      - Removes recipients no longer in HubSpot, but ONLY if they are still 'pending'
        (already-sent recipients are preserved for audit trail)
    """
    from ..services.suppression import is_newsletter_suppressed

    existing = (
        db.query(NewsletterRecipient)
        .filter(NewsletterRecipient.newsletter_id == nl.id)
        .all()
    )
    existing_by_email = {r.contact_email.strip().lower(): r for r in existing}

    added = 0
    removed = 0

    for email, contact in hs_emails.items():
        if email in existing_by_email:
            continue
        if is_newsletter_suppressed(db, email):
            continue
        recip = NewsletterRecipient(
            id=str(uuid.uuid4()),
            newsletter_id=nl.id,
            contact_email=email,
            contact_name=contact.get("name") or None,
            contact_company=contact.get("company_name") or None,
            source="hubspot_sync",
        )
        db.add(recip)
        added += 1

    for email, recip in existing_by_email.items():
        if email not in hs_emails and recip.status == NewsletterRecipientStatus.pending:
            db.delete(recip)
            removed += 1

    nl.hubspot_last_synced_at = datetime.now(timezone.utc)
    db.commit()

    if added or removed:
        logger.info(
            "HubSpot auto-sync: newsletter '%s' — added=%d, removed=%d",
            nl.title, added, removed,
        )


# ── Send service ────────────────────────────────────────────────────────────


class NewsletterSendService:
    """Handles sending newsletters via Microsoft Graph API.

    Stateless service class — instantiate, call send_newsletter() or
    send_test_email(), then discard. Each call manages its own Outlook
    client lifecycle (connect, send, close).

    The send pipeline follows these steps:
      1. Validate the newsletter exists and is in a sendable state
      2. CAS-claim it (status → 'sending') to prevent double-sends
      3. Run suppression sweep to catch late unsubscribes
      4. Loop through pending recipients: render HTML, send via Graph, update status
      5. Finalize: status → 'sent' (or 'failed' if all recipients failed)
    """

    def _fetch_and_validate(self, db: Session, newsletter_id: str) -> "Newsletter":
        """Fetch the newsletter and verify it is in a sendable state.

        Returns the Newsletter ORM object.

        Raises:
            ValueError: If not found or not in a sendable status.
        """
        nl = db.query(Newsletter).filter(Newsletter.id == newsletter_id).first()
        if nl is None:
            raise ValueError("Newsletter not found: {}".format(newsletter_id))

        db.refresh(nl)
        if nl.status not in (NewsletterStatus.draft, NewsletterStatus.scheduled, NewsletterStatus.sending):
            raise ValueError(
                "Newsletter '{}' has status '{}' -- only 'draft', 'scheduled', or 'sending' can be sent.".format(
                    nl.id, nl.status.value,
                )
            )
        if nl.status != NewsletterStatus.sending and not nl.is_sendable:
            raise ValueError(
                "Newsletter '{}' is not sendable (missing subject, owner, or content blocks).".format(nl.id)
            )
        return nl

    def _cas_claim_for_sending(self, db: Session, nl: "Newsletter", newsletter_id: str) -> None:
        """Atomically claim the newsletter for sending via CAS (compare-and-swap).

        SWAT-C3: DB-level CAS to prevent double-send races (scheduler vs manual,
        or scheduler vs scheduler on restart). Only one caller wins the transition.

        Raises:
            ValueError: If another process already claimed it or the transition failed.
        """
        from sqlalchemy import text as _text

        if nl.status == NewsletterStatus.sending:
            return  # Already claimed (e.g. resuming after crash)

        _is_pg = hasattr(db.bind, "dialect") and getattr(db.bind.dialect, "name", "") == "postgresql"
        if _is_pg:
            claimed = db.execute(
                _text(
                    "UPDATE newsletters SET status = 'sending' "
                    "WHERE id = :nid AND status IN ('draft', 'scheduled') "
                    "RETURNING id"
                ),
                {"nid": newsletter_id},
            ).fetchone()
            db.commit()
        else:
            # SQLite fallback: use threading lock (sufficient for single-process tests)
            claimed = None
            with _status_lock:
                db.refresh(nl)
                if nl.status in (NewsletterStatus.draft, NewsletterStatus.scheduled):
                    nl.status = NewsletterStatus.sending
                    db.commit()
                    claimed = True

        if not claimed:
            db.refresh(nl)
            if nl.status == NewsletterStatus.sending:
                raise ValueError(
                    "Newsletter '{}' is already being sent by another process.".format(nl.id)
                )
            raise ValueError(
                "Newsletter '{}' status transition failed.".format(nl.id)
            )

    def _pre_send_checks(self, db: Session, nl: "Newsletter", newsletter_id: str) -> tuple[int, Path]:
        """Run pre-send checks: suppression sweep, recipient count, Outlook token.

        Returns (skipped_unsubscribed_count, outlook_token_file_path).

        Raises:
            ValueError: If no eligible recipients remain or Outlook token is missing.
        """
        skipped_unsubscribed = self._suppression_sweep(db, newsletter_id)

        remaining_pending = (
            db.query(NewsletterRecipient)
            .filter(
                NewsletterRecipient.newsletter_id == newsletter_id,
                NewsletterRecipient.status == NewsletterRecipientStatus.pending,
            )
            .count()
        )
        if remaining_pending == 0:
            with _status_lock:
                db.refresh(nl)
                nl.status = NewsletterStatus.draft
                db.commit()
            raise ValueError("No eligible recipients after suppression sweep")

        owner_user_id = nl.owner_id
        owner_user = db.query(User).filter(User.id == owner_user_id).first()
        from ..api.conference._shared import _outlook_token_file
        token_file = _outlook_token_file(owner_user)
        if not token_file.exists():
            with _status_lock:
                db.refresh(nl)
                nl.status = NewsletterStatus.draft
                db.commit()
            raise ValueError(
                "No Outlook token found for owner '{}'. "
                "The newsletter owner must connect Outlook via the dashboard before sending.".format(
                    owner_user_id,
                )
            )

        return skipped_unsubscribed, token_file

    def _send_to_recipients(
        self, db: Session, nl: "Newsletter", newsletter_id: str, token_file: Path,
    ) -> tuple[int, int]:
        """Send the newsletter to each pending recipient, with per-recipient error handling.

        Returns (sent_count, failed_count).
        """
        pending_recipients = (
            db.query(NewsletterRecipient)
            .filter(
                NewsletterRecipient.newsletter_id == newsletter_id,
                NewsletterRecipient.status == NewsletterRecipientStatus.pending,
            )
            .all()
        )

        sent_count = 0
        failed_count = 0

        for idx, recip in enumerate(pending_recipients):
            if _newsletter_stop.is_set():
                logger.warning(
                    "Newsletter send interrupted by shutdown (id=%s, sent=%d/%d).",
                    newsletter_id, sent_count, len(pending_recipients),
                )
                break

            try:
                from ..services.newsletter_templates import render_newsletter
                html_body = render_newsletter(
                    db,
                    newsletter_id,
                    recipient_email=recip.contact_email,
                    recipient_name=recip.contact_name,
                )

                success = self._send_single(
                    token_file=token_file,
                    to_email=recip.contact_email,
                    to_name=recip.contact_name or "",
                    subject=nl.subject_line or nl.title,
                    html_body=html_body,
                )

                if success:
                    recip.status = NewsletterRecipientStatus.sent
                    recip.sent_at = datetime.now(timezone.utc)
                    sent_count += 1
                else:
                    recip.status = NewsletterRecipientStatus.failed
                    failed_count += 1

            except Exception as exc:
                logger.error(
                    "Newsletter send: failed recipient '%s' (newsletter=%s): %s",
                    recip.contact_email, newsletter_id, exc,
                )
                recip.status = NewsletterRecipientStatus.failed
                failed_count += 1

            db.commit()

            # Rate limit: 1 email per second to avoid Graph throttling.
            # Skip sleep after the last email.
            if idx < len(pending_recipients) - 1:
                time.sleep(1)

        return sent_count, failed_count

    def send_newsletter(self, db: Session, newsletter_id: str) -> dict:
        """Send newsletter to all pending recipients.

        Steps:
            1. Verify status is 'draft' or 'scheduled' (not already sent/sending).
            2. Verify newsletter.is_sendable.
            3. Set status to 'sending'.
            4. Pre-send suppression sweep -- re-check all recipients against
               NewsletterUnsubscribe, mark any newly-unsubscribed as 'unsubscribed'.
            5. For each pending recipient: render, send, rate-limit, update status.
            6. On completion: status='sent', sent_at=now.
            7. If ALL failed: status='failed'.

        Args:
            db: SQLAlchemy session.
            newsletter_id: ID of the Newsletter to send.

        Returns:
            Dict with sent, failed, skipped_unsubscribed counts.

        Raises:
            ValueError: If newsletter not found or not in a sendable state.
        """
        # ── 1. Fetch & validate ───────────────────────────────────────────────
        nl = self._fetch_and_validate(db, newsletter_id)

        # ── 2. CAS claim for sending ─────────────────────────────────────────
        self._cas_claim_for_sending(db, nl, newsletter_id)
        db.refresh(nl)

        # ── 3. Pre-send checks ───────────────────────────────────────────────
        skipped_unsubscribed, token_file = self._pre_send_checks(db, nl, newsletter_id)

        # ── 4. Send loop ─────────────────────────────────────────────────────
        sent_count, failed_count = self._send_to_recipients(db, nl, newsletter_id, token_file)

        # ── 5. Finalize status ────────────────────────────────────────────────
        with _status_lock:
            db.refresh(nl)
            total_attempted = sent_count + failed_count
            if total_attempted > 0 and sent_count == 0:
                nl.status = NewsletterStatus.failed
            else:
                nl.status = NewsletterStatus.sent
                nl.sent_at = datetime.now(timezone.utc)
            db.commit()

        result = {
            "sent": sent_count,
            "failed": failed_count,
            "skipped_unsubscribed": skipped_unsubscribed,
        }
        logger.info(
            "Newsletter send complete (id=%s): %s", newsletter_id, result,
        )
        return result

    def send_test_email(self, db: Session, newsletter_id: str, test_email: str) -> dict:
        """Send a single preview email to test_email. No DB status updates on newsletter.

        Args:
            db: SQLAlchemy session.
            newsletter_id: ID of the Newsletter to preview.
            test_email: Email address to send the test to.

        Returns:
            Dict with status and details.

        Raises:
            ValueError: If newsletter not found or not renderable.
        """
        nl = db.query(Newsletter).filter(Newsletter.id == newsletter_id).first()
        if nl is None:
            raise ValueError("Newsletter not found: {}".format(newsletter_id))

        if not nl.owner_id:
            raise ValueError("Newsletter has no owner -- cannot determine sending account.")

        owner_user = db.query(User).filter(User.id == nl.owner_id).first()
        from ..api.conference._shared import _outlook_token_file
        tf = _outlook_token_file(owner_user)
        if not tf.exists():
            raise ValueError("Outlook not connected for newsletter owner.")

        from ..services.newsletter_templates import render_newsletter
        html_body = render_newsletter(
            db,
            newsletter_id,
            recipient_email=test_email,
            recipient_name="Test Recipient",
        )

        success = self._send_single(
            token_file=tf,
            to_email=test_email,
            to_name="Test Recipient",
            subject="[TEST] {}".format(nl.subject_line or nl.title),
            html_body=html_body,
        )

        return {
            "status": "sent" if success else "failed",
            "to": test_email,
            "newsletter_id": newsletter_id,
        }

    def _send_single(
        self,
        token_file: Path,
        to_email: str,
        to_name: str,
        subject: str,
        html_body: str,
    ) -> bool:
        """Send one email via Graph API sendMail.

        Args:
            token_file: Path to the Outlook token cache file.
            to_email: Recipient email address.
            to_name: Recipient display name.
            subject: Email subject line.
            html_body: Full HTML body content.

        Returns:
            True on success, False on failure.
        """
        from ..services.outlook import OutlookClient, GRAPH_BASE as _GRAPH_BASE

        if not token_file.exists():
            logger.error(
                "Newsletter send: no Outlook token at %s. "
                "User must connect Outlook via the dashboard first.",
                token_file,
            )
            return False

        try:
            client = OutlookClient(token_file=token_file)
        except Exception as exc:
            logger.error("Newsletter send: failed to create OutlookClient: %s", exc)
            return False

        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [
                    {"emailAddress": {"address": to_email, "name": to_name}},
                ],
            },
            "saveToSentItems": True,
        }

        try:
            # Graph's POST /me/sendMail returns 202 Accepted with an empty body.
            # OutlookClient._post tries to parse JSON from the response, which
            # raises RuntimeError on empty-body 202s. We catch that specific
            # error and treat it as success since raise_for_status() passed.
            try:
                client._post("{}/me/sendMail".format(_GRAPH_BASE), payload)
            except RuntimeError as rt_err:
                # If the error is about non-JSON response on a 202, it's success.
                err_msg = str(rt_err)
                if "non-JSON" in err_msg and "202" in err_msg:
                    pass  # 202 Accepted — email sent successfully
                else:
                    raise
            return True
        except Exception as exc:
            logger.error(
                "Newsletter send: Graph API error sending to '%s': %s",
                to_email, exc,
            )
            return False
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _suppression_sweep(self, db: Session, newsletter_id: str) -> int:
        """Re-check all pending recipients against suppression lists.

        Marks any newly-unsubscribed recipients as 'unsubscribed' before
        the main send loop runs.

        Args:
            db: SQLAlchemy session.
            newsletter_id: Newsletter ID.

        Returns:
            Number of recipients marked as unsubscribed.
        """
        pending = (
            db.query(NewsletterRecipient)
            .filter(
                NewsletterRecipient.newsletter_id == newsletter_id,
                NewsletterRecipient.status == NewsletterRecipientStatus.pending,
            )
            .all()
        )

        suppressed_count = 0
        for recip in pending:
            email = recip.contact_email.strip().lower()
            if self._is_suppressed(db, email):
                recip.status = NewsletterRecipientStatus.unsubscribed
                suppressed_count += 1

        if suppressed_count > 0:
            db.commit()
            logger.info(
                "Newsletter pre-send sweep: marked %d recipient(s) as unsubscribed (newsletter=%s).",
                suppressed_count, newsletter_id,
            )
        return suppressed_count

    @staticmethod
    def _is_suppressed(db: Session, email: str) -> bool:
        """Check both newsletter-specific AND campaign-level suppression."""
        from ..services.suppression import is_newsletter_suppressed
        return is_newsletter_suppressed(db, email)


# ── Scheduling helpers ──────────────────────────────────────────────────────


def schedule_newsletter(
    db: Session,
    newsletter_id: str,
    scheduled_at: datetime,
    current_user: User,
) -> dict:
    """Set newsletter to scheduled status with future send time.

    Args:
        db: SQLAlchemy session.
        newsletter_id: Newsletter ID.
        scheduled_at: Future UTC datetime for the send.
        current_user: Authenticated user (must own the newsletter).

    Returns:
        Dict with newsletter_id, status, scheduled_at.

    Raises:
        ValueError: If newsletter not found, not owned, or scheduled_at is in the past.
    """
    nl = db.query(Newsletter).filter(Newsletter.id == newsletter_id).first()
    if nl is None:
        raise ValueError("Newsletter not found: {}".format(newsletter_id))
    if nl.owner_id != current_user.id:
        raise ValueError("Newsletter not found: {}".format(newsletter_id))

    now = datetime.now(timezone.utc)
    # Normalize scheduled_at to UTC-aware if naive
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

    if scheduled_at <= now:
        raise ValueError("scheduled_at must be in the future.")

    with _status_lock:
        db.refresh(nl)
        if nl.status not in (NewsletterStatus.draft, NewsletterStatus.scheduled):
            raise ValueError(
                "Cannot schedule newsletter with status '{}'. Must be 'draft' or 'scheduled'.".format(
                    nl.status.value,
                )
            )
        nl.status = NewsletterStatus.scheduled
        nl.scheduled_at = scheduled_at
        db.commit()

    return {
        "newsletter_id": nl.id,
        "status": nl.status.value,
        "scheduled_at": scheduled_at.isoformat(),
    }


def cancel_schedule(db: Session, newsletter_id: str, current_user: User) -> dict:
    """Cancel scheduled send, return newsletter to draft status.

    Args:
        db: SQLAlchemy session.
        newsletter_id: Newsletter ID.
        current_user: Authenticated user (must own the newsletter).

    Returns:
        Dict with newsletter_id and status.

    Raises:
        ValueError: If newsletter not found, not owned, or not currently scheduled.
    """
    nl = db.query(Newsletter).filter(Newsletter.id == newsletter_id).first()
    if nl is None:
        raise ValueError("Newsletter not found: {}".format(newsletter_id))
    if nl.owner_id != current_user.id:
        raise ValueError("Newsletter not found: {}".format(newsletter_id))

    with _status_lock:
        db.refresh(nl)
        if nl.status != NewsletterStatus.scheduled:
            raise ValueError(
                "Cannot cancel schedule: newsletter status is '{}', not 'scheduled'.".format(
                    nl.status.value,
                )
            )
        nl.status = NewsletterStatus.draft
        nl.scheduled_at = None
        db.commit()

    return {
        "newsletter_id": nl.id,
        "status": "draft",
    }
