"""
Newsletter CRUD, recipient management, send/schedule, and AI composition endpoints.

This is the main API router for the newsletter subsystem. It handles the full
lifecycle of a newsletter: create → add recipients → compose content → preview →
send/schedule → track status. Newsletters target three audience tracks:

  - **prospect**: Contacts in early outreach stages (new, pending, enrolled, etc.)
  - **client**: Contacts who have replied (warmer relationship)
  - **hubspot**: Contacts synced from HubSpot's "To Nurture" deal stage

Key capabilities:
  - CRUD: Create, list, update, delete, duplicate newsletters
  - Recipients: Populate from Prospect table, sync from HubSpot, manual add/remove,
    CSV import — all entry points enforce suppression checks
  - AI Compose: Upload source material (Excel/CSV), call Claude to generate body text,
    iterate with follow-up instructions (full conversation history preserved)
  - Send: Background thread send (returns 202), test send, schedule/cancel schedule
  - Push to Drafts: Creates personalized Outlook drafts (with inline logo/hero images)
    instead of sending directly — useful when the sender wants to review before sending
  - Unsubscribe: Public endpoint (no auth, HMAC-validated) + admin management

Security:
  - All newsletter operations are owner-scoped (owner_id forced to current_user.id)
  - Non-owners get 404 (not 403) to prevent existence leaking
  - Public unsubscribe endpoint rate-limited to 10/minute
  - Content JSON capped at 500KB, CSV at 5MB, content files at 10MB

Relationships:
  - Uses NewsletterSendService (newsletter_sender.py) for actual email delivery
  - Uses newsletter_templates.py for HTML rendering and unsubscribe tokens
  - Uses suppression.py for cross-system unsubscribe checks
  - Uses OutlookClient (outlook.py) for push-to-drafts flow
  - Uses Claude API (anthropic) for AI composition and iteration
"""

import copy
import csv
import html as html_mod
import io
import logging
import math
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import (
    Newsletter,
    NewsletterRecipient,
    NewsletterRecipientStatus,
    NewsletterStatus,
    NewsletterUnsubscribe,
    NewsletterTrack,
    Prospect,
    ProspectStatus,
    UnsubscribeEntry,
    User,
    UserRole,
)
from .auth import get_current_user

router = APIRouter(prefix="/newsletters", tags=["newsletters"])
logger = logging.getLogger(__name__)

# ── Prospect-status filters per track ────────────────────────────────────────
# When populating recipients from the Prospect table, these status sets
# determine which prospects are eligible based on the newsletter's track.
# "prospect" track = contacts still in the outreach funnel (haven't replied yet)
# "client" track = contacts who have replied (warmer, more engaged)

_PROSPECT_TRACK_STATUSES = {
    ProspectStatus.new,
    ProspectStatus.pending,
    ProspectStatus.enrolled,
    ProspectStatus.opened,
    ProspectStatus.connected,
    ProspectStatus.messaged,
}

_CLIENT_TRACK_STATUSES = {
    ProspectStatus.replied,
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_newsletter_or_404(db: Session, newsletter_id: str, current_user: User) -> Newsletter:
    """Fetch newsletter, verify ownership."""
    nl = db.query(Newsletter).filter(Newsletter.id == newsletter_id).first()
    if nl is None:
        raise HTTPException(status_code=404, detail="Newsletter not found")
    if nl.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Newsletter not found")
    return nl


def _is_suppressed(db: Session, email: str) -> bool:
    """Check both newsletter-specific AND campaign-level suppression.

    Delegates to the shared service to avoid circular imports.
    """
    from ..services.suppression import is_newsletter_suppressed
    return is_newsletter_suppressed(db, email)


def _already_recipient(db: Session, newsletter_id: str, email: str) -> bool:
    """Check if email is already a recipient of this newsletter."""
    return (
        db.query(NewsletterRecipient)
        .filter(
            NewsletterRecipient.newsletter_id == newsletter_id,
            NewsletterRecipient.contact_email == email,
        )
        .first()
        is not None
    )


def _nl_to_dict(nl: Newsletter, recipient_count: int = None) -> dict:
    """Serialize a Newsletter ORM object to a dict for JSON response.

    Args:
        nl: Newsletter ORM instance.
        recipient_count: Pre-computed count to avoid N+1 lazy load.
                         If None, falls back to len(nl.recipients).
    """
    if recipient_count is None:
        recipient_count = len(nl.recipients) if nl.recipients is not None else 0
    return {
        "id": nl.id,
        "title": nl.title,
        "track": nl.track.value if nl.track else None,
        "status": nl.status.value if nl.status else None,
        "owner_id": nl.owner_id,
        "subject_line": nl.subject_line,
        "preheader_text": nl.preheader_text,
        "content_json": nl.content_json,
        "scheduled_at": nl.scheduled_at.isoformat() if nl.scheduled_at else None,
        "sent_at": nl.sent_at.isoformat() if nl.sent_at else None,
        "hubspot_last_synced_at": nl.hubspot_last_synced_at.isoformat() if nl.hubspot_last_synced_at else None,
        "created_at": nl.created_at.isoformat() if nl.created_at else None,
        "updated_at": nl.updated_at.isoformat() if nl.updated_at else None,
        "recipient_count": recipient_count,
    }


# ── Service functions ────────────────────────────────────────────────────────

def populate_recipients(db: Session, newsletter_id: str, current_user: User) -> dict:
    """Pull from Prospect table filtered by track, exclude suppressed emails.

    Returns {"added": N, "skipped_duplicates": N, "skipped_unsubscribed": N, "total": N}
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    if nl.track == NewsletterTrack.prospect:
        statuses = _PROSPECT_TRACK_STATUSES
    else:
        statuses = _CLIENT_TRACK_STATUSES

    prospects = (
        db.query(Prospect)
        .filter(
            Prospect.user_id == current_user.id,
            Prospect.status.in_(statuses),
        )
        .all()
    )

    added = 0
    skipped_duplicates = 0
    skipped_unsubscribed = 0

    for p in prospects:
        email = p.email.strip().lower()

        if _already_recipient(db, newsletter_id, email):
            skipped_duplicates += 1
            continue

        if _is_suppressed(db, email):
            skipped_unsubscribed += 1
            continue

        recip = NewsletterRecipient(
            id=str(uuid.uuid4()),
            newsletter_id=newsletter_id,
            contact_email=email,
            contact_name=f"{p.first_name or ''} {p.last_name or ''}".strip() or None,
            contact_company=p.company,
            source="populate",
        )
        db.add(recip)
        added += 1

    db.commit()
    return {
        "added": added,
        "skipped_duplicates": skipped_duplicates,
        "skipped_unsubscribed": skipped_unsubscribed,
        "total": added + skipped_duplicates + skipped_unsubscribed,
    }


def sync_hubspot_recipients(db: Session, newsletter_id: str, current_user: User) -> dict:
    """Full sync: pull contacts from HubSpot 'To Nurture' stage.

    - Adds new contacts as recipients
    - Removes recipients no longer in HubSpot (unless already sent)
    - Skips suppressed emails
    - Updates hubspot_last_synced_at

    Returns {"added": N, "removed": N, "skipped_suppressed": N, "total_hubspot": N, "total_recipients": N}
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    if nl.track != NewsletterTrack.hubspot:
        raise HTTPException(status_code=400, detail="Only hubspot-track newsletters can sync from HubSpot.")

    from ..services.hubspot import HubSpotClient
    try:
        client = HubSpotClient()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    try:
        hubspot_contacts = client.list_nurture_contacts()
    except Exception as exc:
        logger.error("HubSpot sync failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to fetch contacts from HubSpot. Check HUBSPOT_API_TOKEN and try again.")
    finally:
        client.close()

    # Build lookup of HubSpot emails
    hs_emails = {}  # email -> contact dict
    for c in hubspot_contacts:
        email = c["email"].strip().lower()
        if email:
            hs_emails[email] = c

    # Get existing recipients
    existing = (
        db.query(NewsletterRecipient)
        .filter(NewsletterRecipient.newsletter_id == nl.id)
        .all()
    )
    existing_by_email = {r.contact_email.strip().lower(): r for r in existing}

    added = 0
    skipped_suppressed = 0
    removed = 0

    # Add new contacts from HubSpot
    for email, contact in hs_emails.items():
        if email in existing_by_email:
            continue  # already a recipient

        if _is_suppressed(db, email):
            skipped_suppressed += 1
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

    # Remove recipients no longer in HubSpot (only if pending — don't remove already-sent)
    for email, recip in existing_by_email.items():
        if email not in hs_emails and recip.status == NewsletterRecipientStatus.pending:
            db.delete(recip)
            removed += 1

    # Update sync timestamp
    nl.hubspot_last_synced_at = datetime.now(timezone.utc)
    db.commit()

    total_recipients = (
        db.query(NewsletterRecipient)
        .filter(NewsletterRecipient.newsletter_id == nl.id)
        .count()
    )

    result = {
        "added": added,
        "removed": removed,
        "skipped_suppressed": skipped_suppressed,
        "total_hubspot": len(hs_emails),
        "total_recipients": total_recipients,
    }
    logger.info("HubSpot sync complete (newsletter=%s): %s", nl.id, result)
    return result


def add_recipient(db: Session, newsletter_id: str, email: str, name: Optional[str], company: Optional[str], current_user: User) -> dict:
    """Manual add -- check suppression first."""
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)
    email = email.strip().lower()

    if _is_suppressed(db, email):
        raise HTTPException(status_code=409, detail="Email is suppressed/unsubscribed")

    if _already_recipient(db, nl.id, email):
        raise HTTPException(status_code=409, detail="Email already a recipient of this newsletter")

    recip = NewsletterRecipient(
        id=str(uuid.uuid4()),
        newsletter_id=nl.id,
        contact_email=email,
        contact_name=name,
        contact_company=company,
        source="manual",
    )
    db.add(recip)
    db.commit()
    return {
        "id": recip.id,
        "contact_email": recip.contact_email,
        "contact_name": recip.contact_name,
        "contact_company": recip.contact_company,
        "status": "added",
    }


def remove_recipient(db: Session, newsletter_id: str, email: str, current_user: User) -> dict:
    """Remove a single recipient."""
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)
    email = email.strip().lower()

    recip = (
        db.query(NewsletterRecipient)
        .filter(
            NewsletterRecipient.newsletter_id == nl.id,
            NewsletterRecipient.contact_email == email,
        )
        .first()
    )
    if recip is None:
        raise HTTPException(status_code=404, detail="Recipient not found")

    db.delete(recip)
    db.commit()
    return {"email": email, "status": "removed"}


_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Max content_json payload size (500 KB serialized).
_CONTENT_JSON_MAX_BYTES = 500 * 1024


def _validate_content_json(cj: object) -> None:
    """Raise HTTPException if content_json is too large."""
    if cj is None:
        return
    import json
    raw = json.dumps(cj) if not isinstance(cj, str) else cj
    if len(raw.encode("utf-8")) > _CONTENT_JSON_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail="content_json is too large (max {} KB).".format(_CONTENT_JSON_MAX_BYTES // 1024),
        )


def import_csv_recipients(db: Session, newsletter_id: str, file_content: str, current_user: User) -> dict:
    """Import from CSV -- dedup + suppression."""
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    _CSV_MAX_ROWS = 5000

    reader = csv.DictReader(io.StringIO(file_content))
    added = 0
    skipped_duplicates = 0
    skipped_unsubscribed = 0
    errors = 0
    row_count = 0

    for row in reader:
        row_count += 1
        if row_count > _CSV_MAX_ROWS:
            raise HTTPException(
                status_code=400,
                detail="CSV exceeds maximum of {} rows. Please split into smaller files.".format(_CSV_MAX_ROWS),
            )
        email = (row.get("email") or "").strip().lower()
        if not email or not _EMAIL_RE.match(email):
            errors += 1
            continue

        if _already_recipient(db, nl.id, email):
            skipped_duplicates += 1
            continue

        if _is_suppressed(db, email):
            skipped_unsubscribed += 1
            continue

        recip = NewsletterRecipient(
            id=str(uuid.uuid4()),
            newsletter_id=nl.id,
            contact_email=email,
            contact_name=row.get("name", "").strip() or None,
            contact_company=row.get("company", "").strip() or None,
            source="csv_import",
        )
        db.add(recip)
        added += 1

    db.commit()
    return {
        "added": added,
        "skipped_duplicates": skipped_duplicates,
        "skipped_unsubscribed": skipped_unsubscribed,
        "errors": errors,
        "total": added + skipped_duplicates + skipped_unsubscribed + errors,
    }


# ── Pydantic request/response models ────────────────────────────────────────

class AddRecipientRequest(BaseModel):
    email: str
    name: Optional[str] = None
    company: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address.")
        return v


class CreateNewsletterRequest(BaseModel):
    """Request body for creating a newsletter."""
    title: str
    track: str  # "prospect", "client", or "hubspot"
    subject_line: Optional[str] = None
    preheader_text: Optional[str] = None
    content_json: object = None  # dict (body_text) or legacy list of blocks
    owner_id: Optional[str] = None  # ignored -- forced to current_user.id


class UpdateNewsletterRequest(BaseModel):
    """Request body for updating a newsletter."""
    title: Optional[str] = None
    subject_line: Optional[str] = None
    preheader_text: Optional[str] = None
    content_json: object = None  # dict (body_text) or legacy list of blocks
    track: Optional[str] = None
    owner_id: Optional[str] = None


class SendTestRequest(BaseModel):
    """Request body for sending a test email."""
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address.")
        return v


class ScheduleRequest(BaseModel):
    """Request body for scheduling a newsletter send."""
    scheduled_at: str  # ISO 8601 datetime string


class AdminUnsubscribeRequest(BaseModel):
    """Request body for manually adding an email to the unsubscribe list."""
    email: str
    reason: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address.")
        return v


# ── CRUD endpoints (static paths first, then dynamic) ────────────────────────
# IMPORTANT: FastAPI route ordering matters. Static paths like /unsubscribes
# must be registered BEFORE dynamic paths like /{newsletter_id}.


@router.get("/unsubscribes")
def list_unsubscribes(
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Paginated list of all newsletter-unsubscribed emails.

    Args:
        page: Page number (1-indexed).
        per_page: Items per page (max 100).
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Dict with page, per_page, total, and items list.
    """
    query = db.query(NewsletterUnsubscribe).order_by(NewsletterUnsubscribe.email)
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "items": [
            {
                "id": u.id,
                "email": u.email,
                "reason": u.reason,
                "unsubscribed_at": u.unsubscribed_at.isoformat() if u.unsubscribed_at else None,
            }
            for u in items
        ],
    }


@router.post("/unsubscribes")
def admin_add_unsubscribe(
    body: AdminUnsubscribeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Manually add an email to the newsletter unsubscribe list.

    Args:
        body: Request with email and optional reason.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Dict with the created unsubscribe record.

    Raises:
        HTTPException 409: If email is already unsubscribed.
    """
    email_clean = body.email.strip().lower()

    existing = (
        db.query(NewsletterUnsubscribe)
        .filter(NewsletterUnsubscribe.email == email_clean)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Email already unsubscribed")

    unsub = NewsletterUnsubscribe(
        id=str(uuid.uuid4()),
        email=email_clean,
        reason=body.reason or "admin_manual",
    )
    db.add(unsub)

    # Also mark all recipient rows across newsletters
    from ..db.models import NewsletterRecipientStatus
    db.query(NewsletterRecipient).filter(
        NewsletterRecipient.contact_email == email_clean,
    ).update(
        {"status": NewsletterRecipientStatus.unsubscribed},
        synchronize_session="fetch",
    )

    db.commit()
    return {
        "id": unsub.id,
        "email": unsub.email,
        "reason": unsub.reason,
        "status": "added",
    }


@router.delete("/unsubscribes/{email:path}")
def admin_remove_unsubscribe(
    email: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Remove an email from the newsletter unsubscribe list (re-subscribe).

    Args:
        email: The email address to remove from unsubscribe list.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Dict with email and status.

    Raises:
        HTTPException 404: If email not found in unsubscribe list.
    """
    email_clean = email.strip().lower()
    unsub = (
        db.query(NewsletterUnsubscribe)
        .filter(NewsletterUnsubscribe.email == email_clean)
        .first()
    )
    if unsub is None:
        raise HTTPException(status_code=404, detail="Email not found in unsubscribe list")

    db.delete(unsub)

    # Re-activate recipient rows for newsletters that haven't been sent yet
    draft_nl_ids = [
        nid for (nid,) in db.query(Newsletter.id).filter(
            Newsletter.status.in_([NewsletterStatus.draft, NewsletterStatus.scheduled]),
        ).all()
    ]
    reactivated = 0
    if draft_nl_ids:
        reactivated = db.query(NewsletterRecipient).filter(
            NewsletterRecipient.contact_email == email_clean,
            NewsletterRecipient.status == NewsletterRecipientStatus.unsubscribed,
            NewsletterRecipient.newsletter_id.in_(draft_nl_ids),
        ).update(
            {"status": NewsletterRecipientStatus.pending},
            synchronize_session="fetch",
        )

    db.commit()
    return {"email": email_clean, "status": "removed", "reactivated": reactivated}


# ── FIX-8: Newsletter CRUD endpoints ────────────────────────────────────────


@router.post("")
def create_newsletter(
    body: CreateNewsletterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Create a new newsletter draft.

    owner_id is always forced to current_user.id regardless of request body.

    Args:
        body: Newsletter creation payload.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Dict with the created newsletter.
    """
    # Validate track
    try:
        track = NewsletterTrack(body.track)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid track '{}'. Must be 'prospect', 'client', or 'hubspot'.".format(body.track),
        )

    _validate_content_json(body.content_json)

    nl = Newsletter(
        id=str(uuid.uuid4()),
        title=body.title,
        track=track,
        owner_id=current_user.id,  # FIX-3: forced to current user
        subject_line=body.subject_line,
        preheader_text=body.preheader_text,
        content_json=body.content_json or {},
        status=NewsletterStatus.draft,
    )
    db.add(nl)
    db.commit()
    db.refresh(nl)
    return _nl_to_dict(nl)


@router.get("")
def list_newsletters(
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    track: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List newsletters for the current user, with optional track/status filters.

    Args:
        page: Page number.
        per_page: Items per page.
        track: Optional track filter ('prospect' or 'client').
        status: Optional status filter.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Paginated dict with items list.
    """
    # Subquery count to avoid N+1 lazy load on recipients
    recip_count_sub = (
        db.query(
            NewsletterRecipient.newsletter_id,
            func.count(NewsletterRecipient.id).label("cnt"),
        )
        .group_by(NewsletterRecipient.newsletter_id)
        .subquery()
    )

    query = (
        db.query(Newsletter, func.coalesce(recip_count_sub.c.cnt, 0).label("recip_cnt"))
        .outerjoin(recip_count_sub, Newsletter.id == recip_count_sub.c.newsletter_id)
        .filter(Newsletter.owner_id == current_user.id)
    )

    if track:
        try:
            track_enum = NewsletterTrack(track)
            query = query.filter(Newsletter.track == track_enum)
        except ValueError:
            pass  # ignore invalid track filter

    if status:
        try:
            status_enum = NewsletterStatus(status)
            query = query.filter(Newsletter.status == status_enum)
        except ValueError:
            pass  # ignore invalid status filter

    query = query.order_by(Newsletter.created_at.desc())
    total = query.count()
    rows = query.offset((page - 1) * per_page).limit(per_page).all()

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": math.ceil(total / per_page) if total > 0 else 1,
        "items": [_nl_to_dict(nl, recipient_count=cnt) for nl, cnt in rows],
    }


# ── Dynamic /{newsletter_id} endpoints ───────────────────────────────────────


@router.delete("/clear")
def delete_all_newsletters(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete all newsletters owned by the current user (except those actively sending)."""
    newsletters = (
        db.query(Newsletter)
        .filter(
            Newsletter.owner_id == current_user.id,
            Newsletter.status != NewsletterStatus.sending,
        )
        .all()
    )
    count = len(newsletters)
    for nl in newsletters:
        db.delete(nl)
    db.commit()
    return {"deleted": count}


@router.get("/{newsletter_id}")
def get_newsletter(
    newsletter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get a single newsletter with recipient stats.

    Args:
        newsletter_id: Newsletter ID.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Newsletter dict with recipient_stats.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)
    result = _nl_to_dict(nl)

    # Recipient stats via GROUP BY
    rows = (
        db.query(NewsletterRecipient.status, func.count(NewsletterRecipient.id))
        .filter(NewsletterRecipient.newsletter_id == nl.id)
        .group_by(NewsletterRecipient.status)
        .all()
    )
    stats = {}
    total = 0
    for status_val, cnt in rows:
        key = status_val.value if status_val else "unknown"
        stats[key] = cnt
        total += cnt
    stats["total"] = total
    result["recipient_stats"] = stats

    return result


@router.patch("/{newsletter_id}")
def update_newsletter(
    newsletter_id: str,
    body: UpdateNewsletterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Update a newsletter. Editing a sent newsletter resets it to draft.

    Args:
        newsletter_id: Newsletter ID.
        body: Fields to update.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Updated newsletter dict.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    # Allow editing sent newsletters by resetting to draft
    if nl.status == NewsletterStatus.sending:
        raise HTTPException(
            status_code=400,
            detail="Cannot update newsletter while it is sending.",
        )
    if nl.status in (NewsletterStatus.sent, NewsletterStatus.failed):
        nl.status = NewsletterStatus.draft

    if body.content_json is not None:
        _validate_content_json(body.content_json)

    if body.title is not None:
        nl.title = body.title
    if body.subject_line is not None:
        nl.subject_line = body.subject_line
    if body.preheader_text is not None:
        nl.preheader_text = body.preheader_text
    if body.content_json is not None:
        # Merge incoming keys into existing dict to preserve source_data/compose_history
        existing_cj = nl.content_json or {}
        if not isinstance(existing_cj, dict):
            existing_cj = {}
        if isinstance(body.content_json, dict):
            existing_cj.update(body.content_json)
            nl.content_json = existing_cj
        else:
            nl.content_json = body.content_json
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(nl, "content_json")
    if body.track is not None:
        try:
            nl.track = NewsletterTrack(body.track)
        except ValueError:
            raise HTTPException(400, "Invalid track. Use 'prospect', 'client', or 'hubspot'.")
    if body.owner_id is not None:
        if body.owner_id != current_user.id:
            raise HTTPException(403, "Can only set yourself as owner.")
        nl.owner_id = body.owner_id

    db.commit()
    db.refresh(nl)
    return _nl_to_dict(nl)


@router.delete("/{newsletter_id}")
def delete_newsletter(
    newsletter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete a newsletter. Cannot delete while actively sending.

    Args:
        newsletter_id: Newsletter ID.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Dict with newsletter_id and status.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    if nl.status == NewsletterStatus.sending:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete newsletter while it is sending.",
        )

    db.delete(nl)
    db.commit()
    return {"newsletter_id": newsletter_id, "status": "deleted"}


@router.post("/{newsletter_id}/duplicate")
def duplicate_newsletter(
    newsletter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Duplicate a newsletter: copy content into a fresh draft with no recipients.

    Args:
        newsletter_id: Source newsletter ID.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        The new newsletter dict.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    # Strip existing trailing " (copy)" to avoid accumulation
    base_title = re.sub(r"\s*\(copy\)$", "", nl.title)
    new_nl = Newsletter(
        id=str(uuid.uuid4()),
        title="{} (copy)".format(base_title),
        track=nl.track,
        owner_id=current_user.id,
        subject_line=nl.subject_line,
        preheader_text=nl.preheader_text,
        content_json=copy.deepcopy(nl.content_json) if nl.content_json else {},
        status=NewsletterStatus.draft,
    )
    db.add(new_nl)
    db.commit()
    db.refresh(new_nl)
    return _nl_to_dict(new_nl)


# ── Recipient endpoints ──────────────────────────────────────────────────────


@router.get("/{newsletter_id}/recipients")
def list_recipients(
    newsletter_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Paginated list of recipients for a newsletter.

    Optional search param filters by email, name, or company (case-insensitive).
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    query = (
        db.query(NewsletterRecipient)
        .filter(NewsletterRecipient.newsletter_id == nl.id)
    )

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            func.lower(NewsletterRecipient.contact_email).like(term)
            | func.lower(func.coalesce(NewsletterRecipient.contact_name, "")).like(term)
            | func.lower(func.coalesce(NewsletterRecipient.contact_company, "")).like(term)
        )

    query = query.order_by(NewsletterRecipient.contact_email)
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "items": [
            {
                "id": r.id,
                "contact_email": r.contact_email,
                "contact_name": r.contact_name,
                "contact_company": r.contact_company,
                "source": r.source,
                "status": r.status.value if r.status else None,
            }
            for r in items
        ],
    }


@router.post("/{newsletter_id}/recipients/populate")
def populate_endpoint(
    newsletter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pull recipients from the Prospect table based on newsletter track."""
    stats = populate_recipients(db, newsletter_id, current_user)
    return stats


@router.post("/{newsletter_id}/sync-hubspot")
def sync_hubspot_endpoint(
    newsletter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sync recipients from HubSpot 'To Nurture' deal stage. Full bidirectional sync."""
    return sync_hubspot_recipients(db, newsletter_id, current_user)


@router.post("/{newsletter_id}/recipients")
def add_recipient_endpoint(
    newsletter_id: str,
    body: AddRecipientRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually add a single recipient."""
    return add_recipient(db, newsletter_id, body.email, body.name, body.company, current_user)


@router.delete("/{newsletter_id}/recipients/clear")
def clear_all_recipients_endpoint(
    newsletter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove all recipients from a newsletter."""
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)
    count = (
        db.query(NewsletterRecipient)
        .filter(NewsletterRecipient.newsletter_id == nl.id)
        .delete(synchronize_session="fetch")
    )
    db.commit()
    return {"removed": count, "newsletter_id": nl.id}


@router.delete("/{newsletter_id}/recipients/{email:path}")
def remove_recipient_endpoint(
    newsletter_id: str,
    email: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a single recipient by email."""
    return remove_recipient(db, newsletter_id, email, current_user)


_CSV_MAX_SIZE = 5 * 1024 * 1024  # 5 MB


@router.post("/{newsletter_id}/recipients/import")
async def import_csv_endpoint(
    newsletter_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import recipients from a CSV file (max 5 MB)."""
    content = await file.read()
    if len(content) > _CSV_MAX_SIZE:
        raise HTTPException(status_code=400, detail="CSV file too large (max 5 MB).")
    file_content = content.decode("utf-8-sig")
    return import_csv_recipients(db, newsletter_id, file_content, current_user)


# ── Newsletter owners endpoint ──────────────────────────────────────────────

_owners_router = APIRouter(prefix="/newsletter-owners", tags=["newsletters"])


@_owners_router.get("")
def list_newsletter_owners(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return list of eligible newsletter owners (admins + current user).

    FIX-4: Only return users with role='admin' or the current user.
    Also includes has_outlook field.
    """
    query = db.query(User).filter(
        (User.role == UserRole.admin) | (User.id == current_user.id)
    ).order_by(User.name)
    users = query.all()

    from .conference._shared import _outlook_token_file

    result = []
    for u in users:
        token_file = _outlook_token_file(u)
        result.append({
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "has_outlook": token_file.exists(),
        })
    return result


# ── Newsletter preview / render endpoints ────────────────────────────────────

@router.get("/{newsletter_id}/preview")
def preview_endpoint(
    newsletter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Render a preview of the newsletter HTML."""
    _get_newsletter_or_404(db, newsletter_id, current_user)
    from ..services.newsletter_templates import preview_newsletter
    html_content = preview_newsletter(db, newsletter_id)
    return HTMLResponse(content=html_content)


owners_router = _owners_router


# ── Unsubscribe HTML pages ──────────────────────────────────────────────────

_UNSUB_SUPPORT_EMAIL = os.getenv("NEWSLETTER_SUPPORT_EMAIL", "demo-member-012@company.example")

_UNSUB_PAGE_STYLE = (
    "body{margin:0;padding:0;background:#F7F7F5;font-family:-apple-system,BlinkMacSystemFont,"
    "'Segoe UI',Roboto,Arial,sans-serif;color:#1A1A1A;display:flex;align-items:center;"
    "justify-content:center;min-height:100vh;}"
    ".card{max-width:480px;width:90%;background:#fff;border:1px solid #E8E8E3;"
    "border-radius:8px;padding:48px 40px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.04);}"
    "h1{font-size:22px;font-weight:700;margin:0 0 16px 0;color:#1A1A1A;}"
    "p{font-size:15px;line-height:1.7;color:#555;margin:0 0 12px 0;}"
    ".accent{color:#2DBDB6;}"
    ".muted{font-size:13px;color:#999;}"
    "a{color:#2DBDB6;text-decoration:none;}"
    "a:hover{text-decoration:underline;}"
)


def _unsub_confirmation_html(email: str) -> str:
    """Render branded unsubscribe confirmation page.

    Args:
        email: The unsubscribed email address (HTML-escaped in output).

    Returns:
        Complete HTML page string.
    """
    safe_email = html_mod.escape(email)
    support = html_mod.escape(_UNSUB_SUPPORT_EMAIL)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1.0'>"
        "<title>Unsubscribed</title>"
        "<style>{style}</style></head>"
        "<body><div class='card'>"
        "<div class='accent' style='font-size:48px;margin-bottom:20px;'>&#10003;</div>"
        "<h1>You've been unsubscribed</h1>"
        "<p>The email <strong>{email}</strong> has been removed from our newsletter list.</p>"
        "<p>You won't receive any more newsletters from us.</p>"
        "<p class='muted'>If this was a mistake, contact "
        "<a href='mailto:{support}'>{support}</a>.</p>"
        "</div></body></html>"
    ).format(style=_UNSUB_PAGE_STYLE, support=support, email=safe_email)


def _unsub_error_html() -> str:
    """Render branded unsubscribe error page.

    Returns:
        Complete HTML page string with generic error message.
    """
    support = html_mod.escape(_UNSUB_SUPPORT_EMAIL)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1.0'>"
        "<title>Error</title>"
        "<style>{style}</style></head>"
        "<body><div class='card'>"
        "<div style='font-size:48px;margin-bottom:20px;color:#999;'>&#9888;</div>"
        "<h1>Something went wrong</h1>"
        "<p>This unsubscribe link may be invalid or expired.</p>"
        "<p class='muted'>Need help? Contact "
        "<a href='mailto:{support}'>{support}</a>.</p>"
        "</div></body></html>"
    ).format(style=_UNSUB_PAGE_STYLE, support=support)


# ── Public unsubscribe router (NO AUTH) ─────────────────────────────────────

unsub_public_router = APIRouter(prefix="/newsletters", tags=["newsletters"])
_unsub_limiter = Limiter(key_func=get_remote_address)


@unsub_public_router.get("/unsubscribe", response_class=HTMLResponse)
@_unsub_limiter.limit("10/minute")
def unsubscribe(
    request: Request,
    token: str,
    email: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Process newsletter unsubscribe. No auth required -- validates HMAC token.

    Args:
        token: HMAC-SHA256 hex token.
        email: Recipient email address.
        db: Database session.

    Returns:
        HTMLResponse with confirmation or error page.
    """
    from ..services.newsletter_templates import verify_unsubscribe_token

    email_clean = email.strip().lower()

    if not verify_unsubscribe_token(email_clean, token):
        return HTMLResponse(content=_unsub_error_html(), status_code=400)

    # Idempotent insert into NewsletterUnsubscribe
    existing = (
        db.query(NewsletterUnsubscribe)
        .filter(NewsletterUnsubscribe.email == email_clean)
        .first()
    )
    if not existing:
        db.add(NewsletterUnsubscribe(
            id=str(uuid.uuid4()),
            email=email_clean,
            reason="email_link",
        ))

    # Mark ALL recipient rows across ALL newsletters as unsubscribed
    from ..db.models import NewsletterRecipientStatus
    db.query(NewsletterRecipient).filter(
        NewsletterRecipient.contact_email == email_clean,
    ).update(
        {"status": NewsletterRecipientStatus.unsubscribed},
        synchronize_session="fetch",
    )

    db.commit()
    return HTMLResponse(content=_unsub_confirmation_html(email_clean), status_code=200)


# ── Send / Schedule / Status endpoints ───────────────────────────────────────


def _send_newsletter_background(newsletter_id: str) -> None:
    """Run send_newsletter in a background thread with its own DB session.

    Args:
        newsletter_id: ID of the newsletter to send.
    """
    from ..db.database import SessionLocal
    from ..services.newsletter_sender import NewsletterSendService

    db = SessionLocal()
    try:
        service = NewsletterSendService()
        service.send_newsletter(db, newsletter_id)
    except Exception as exc:
        logger.error(
            "Background newsletter send failed (id=%s): %s",
            newsletter_id, exc, exc_info=True,
        )
    finally:
        db.close()


class PushDraftsRequest(BaseModel):
    """Request body for push-to-drafts."""
    body_text: str  # The newsletter body text (plain text or HTML)
    raw_html: bool = False  # If True, body_text is complete HTML — skip template wrapping


@router.post("/{newsletter_id}/push-drafts")
def push_to_drafts_endpoint(
    newsletter_id: str,
    body: PushDraftsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Create personalized email drafts in the user's Outlook Drafts folder.

    For each recipient, replaces {{name}} and {{company}} in the body text,
    then creates a draft via Graph API.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    if not nl.subject_line:
        raise HTTPException(status_code=400, detail="Subject line is required.")

    # FIX-5: Suppression sweep before draft creation
    pending_for_sweep = (
        db.query(NewsletterRecipient)
        .filter(
            NewsletterRecipient.newsletter_id == nl.id,
            NewsletterRecipient.status == NewsletterRecipientStatus.pending,
        )
        .all()
    )
    sweep_count = 0
    for recip in pending_for_sweep:
        if _is_suppressed(db, recip.contact_email.strip().lower()):
            recip.status = NewsletterRecipientStatus.unsubscribed
            sweep_count += 1
    if sweep_count > 0:
        db.commit()
        logger.info(
            "Push-to-drafts suppression sweep: marked %d recipient(s) as unsubscribed (newsletter=%s).",
            sweep_count, nl.id,
        )

    # Get recipients (re-query after sweep)
    recipients = (
        db.query(NewsletterRecipient)
        .filter(
            NewsletterRecipient.newsletter_id == nl.id,
            NewsletterRecipient.status == NewsletterRecipientStatus.pending,
        )
        .all()
    )

    if not recipients:
        raise HTTPException(status_code=400, detail="No recipients to send to.")

    # Check Outlook token (reuse conference token resolution for path consistency)
    from .conference._shared import _outlook_token_file
    token_file = _outlook_token_file(current_user)
    if not token_file.exists():
        raise HTTPException(
            status_code=400,
            detail="No Outlook connection found. Please connect your email first.",
        )

    from ..services.outlook import OutlookClient

    try:
        client = OutlookClient(token_file=token_file)
    except Exception as exc:
        logger.error("Push-to-drafts Outlook connection failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to connect to Outlook. Please reconnect your account.")

    from ..services.newsletter_templates import (
        _field_notes_template,
        _plain_text_to_html,
        build_unsubscribe_url,
    )

    created = 0
    failed = 0
    for recip in recipients:
        # Personalize body text
        personalized_body = body.body_text
        name = recip.contact_name or ""
        company = recip.contact_company or ""
        personalized_body = personalized_body.replace("{{name}}", name)
        personalized_body = personalized_body.replace("{{company}}", company)

        if body.raw_html:
            # raw_html mode: body_text is already complete HTML, just personalize
            unsub_url = build_unsubscribe_url(recip.contact_email)
            full_html = personalized_body.replace("{{unsubscribe_url}}", unsub_url)
        else:
            # Standard mode: wrap plain text in the branded template
            body_html = _plain_text_to_html(personalized_body)
            unsub_url = build_unsubscribe_url(recip.contact_email)
            full_html = _field_notes_template(
                subject=nl.subject_line,
                body_html=body_html,
                unsubscribe_url=unsub_url,
                recipient_name=name,
            )

        try:
            result = client.create_draft(
                to_email=recip.contact_email,
                to_name=name,
                subject=nl.subject_line,
                body=full_html,
            )
            # Attach inline logo if the HTML references cid:invigilo-logo
            msg_id = result.get("message_id")
            if msg_id and "cid:invigilo-logo" in full_html:
                try:
                    logo_path = Path(__file__).parent.parent.parent / "dashboard" / "portfolio-logo.svg"
                    if logo_path.exists():
                        client.add_inline_attachment(
                            message_id=msg_id,
                            filename="portfolio-logo.svg",
                            content_bytes=logo_path.read_bytes(),
                            content_type="image/png",
                            content_id="invigilo-logo",
                        )
                except Exception as att_err:
                    logger.warning("Push-to-drafts: logo attach failed for '%s': %s", recip.contact_email, att_err)
            # Attach hero image if referenced
            if msg_id and "cid:hero-image" in full_html:
                try:
                    hero_path = Path(__file__).parent.parent.parent / ".tmp" / "hero_image.jpg"
                    if hero_path.exists():
                        client.add_inline_attachment(
                            message_id=msg_id,
                            filename="hero_image.jpg",
                            content_bytes=hero_path.read_bytes(),
                            content_type="image/jpeg",
                            content_id="hero-image",
                        )
                except Exception as att_err:
                    logger.warning("Push-to-drafts: hero attach failed for '%s': %s", recip.contact_email, att_err)
            recip.status = NewsletterRecipientStatus.sent
            created += 1
        except Exception as exc:
            logger.error("Push-to-drafts failed for '%s': %s", recip.contact_email, exc)
            recip.status = NewsletterRecipientStatus.failed
            failed += 1

    # Mark newsletter status based on results
    if created == 0 and failed > 0:
        nl.status = NewsletterStatus.failed
    else:
        nl.status = NewsletterStatus.sent
    nl.sent_at = datetime.now(timezone.utc)
    db.commit()

    try:
        client.close()
    except Exception:
        pass

    return {"created": created, "failed": failed, "total": created + failed}


@router.post("/{newsletter_id}/send")
def send_newsletter_endpoint(
    newsletter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """Validate and dispatch newsletter send to a background thread.

    FIX-2: Non-blocking -- transitions to 'sending' and returns 202 immediately.
    FIX-3: Validates owner_id matches current_user.id.

    Args:
        newsletter_id: Newsletter ID.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        202 JSONResponse with newsletter_id and status.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    # Defense-in-depth: _get_newsletter_or_404 already checks ownership
    if nl.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the newsletter owner can send.")

    # Validate sendable
    if not nl.is_sendable:
        raise HTTPException(
            status_code=400,
            detail="Newsletter is not sendable (missing subject, owner, or content blocks).",
        )

    # FIX-4: Check recipient count before starting background thread
    pending_count = (
        db.query(func.count(NewsletterRecipient.id))
        .filter(
            NewsletterRecipient.newsletter_id == nl.id,
            NewsletterRecipient.status == NewsletterRecipientStatus.pending,
        )
        .scalar()
    )
    if not pending_count:
        raise HTTPException(
            status_code=400,
            detail="No pending recipients. Add recipients before sending.",
        )

    # Atomic status transition to prevent double-send race condition.
    # Use SELECT ... FOR UPDATE pattern via DB-level refresh + immediate commit.
    db.refresh(nl, with_for_update=True)
    if nl.status not in (NewsletterStatus.draft, NewsletterStatus.scheduled):
        raise HTTPException(
            status_code=400,
            detail="Newsletter status is '{}' -- only 'draft' or 'scheduled' can be sent.".format(
                nl.status.value,
            ),
        )

    nl.status = NewsletterStatus.sending
    db.commit()

    # Dispatch to background thread (matches project pattern from jobs.py)
    thread = threading.Thread(
        target=_send_newsletter_background,
        args=(nl.id,),
        name="newsletter-send-{}".format(nl.id[:8]),
        daemon=True,
    )
    thread.start()

    return JSONResponse(
        status_code=202,
        content={"status": "sending", "newsletter_id": nl.id},
    )


@router.post("/{newsletter_id}/send-test")
def send_test_endpoint(
    newsletter_id: str,
    body: SendTestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Send a single test/preview email to the specified address.

    Args:
        newsletter_id: Newsletter ID.
        body: Request with target email.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Dict with status and recipient email.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)
    from ..services.newsletter_sender import NewsletterSendService
    service = NewsletterSendService()
    try:
        result = service.send_test_email(db, nl.id, body.email.strip().lower())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.post("/{newsletter_id}/schedule")
def schedule_endpoint(
    newsletter_id: str,
    body: ScheduleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Schedule a newsletter for future send.

    FIX-3: Validates owner_id matches current_user.id.

    Args:
        newsletter_id: Newsletter ID.
        body: Request with scheduled_at ISO datetime.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Dict with newsletter_id, status, scheduled_at.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    # Defense-in-depth: _get_newsletter_or_404 already checks ownership
    if nl.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the newsletter owner can schedule.")

    try:
        scheduled_at = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid datetime format. Use ISO 8601.")

    from ..services.newsletter_sender import schedule_newsletter
    try:
        result = schedule_newsletter(db, newsletter_id, scheduled_at, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.post("/{newsletter_id}/cancel-schedule")
def cancel_schedule_endpoint(
    newsletter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Cancel a scheduled newsletter send, returning it to draft status.

    Args:
        newsletter_id: Newsletter ID.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Dict with newsletter_id and status.
    """
    _get_newsletter_or_404(db, newsletter_id, current_user)
    from ..services.newsletter_sender import cancel_schedule
    try:
        result = cancel_schedule(db, newsletter_id, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.get("/{newsletter_id}/send-status")
def send_status_endpoint(
    newsletter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return real-time send status with per-status recipient counts.

    FIX-9: Uses SQL GROUP BY instead of loading all rows.

    Args:
        newsletter_id: Newsletter ID.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Dict with status, total, sent, failed, pending, unsubscribed counts.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    rows = (
        db.query(NewsletterRecipient.status, func.count(NewsletterRecipient.id))
        .filter(NewsletterRecipient.newsletter_id == nl.id)
        .group_by(NewsletterRecipient.status)
        .all()
    )

    counts = {}
    total = 0
    for status_val, cnt in rows:
        key = status_val.value if status_val else "unknown"
        counts[key] = cnt
        total += cnt

    return {
        "status": nl.status.value if nl.status else None,
        "total": total,
        "sent": counts.get("sent", 0),
        "failed": counts.get("failed", 0),
        "pending": counts.get("pending", 0),
        "unsubscribed": counts.get("unsubscribed", 0),
    }


# ── AI Compose endpoints ─────────────────────────────────────────────────────
#
# The newsletter composition flow:
#   1. Upload source material (Excel/CSV with topics, stats, news) via /upload-content
#   2. Call /compose with optional custom prompt → Claude generates body_text
#   3. Iterate with /iterate to refine (full conversation history preserved)
#   4. The body_text is stored in content_json.body_text for rendering
#
# The compose prompt combines:
#   - Newsletter copy rules (from settings, per-user with admin fallback)
#   - User's StyleProfile voice characteristics (from style sync)
#   - Uploaded source data (first 50 rows, to avoid token explosion)
#   - Optional reference files (up to 5, max 5000 chars each)
#   - Custom prompt instructions from the user

_CONTENT_FILE_MAX_SIZE = 10 * 1024 * 1024  # 10 MB


@router.post("/{newsletter_id}/upload-content")
async def upload_content_file(
    newsletter_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Upload an Excel/CSV file with content material for AI newsletter composition.

    The file is parsed and stored as structured data in content_json.source_data.
    This is NOT contacts — this is the raw material (topics, stats, news) the AI
    uses to compose the newsletter body.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    content = await file.read()
    if len(content) > _CONTENT_FILE_MAX_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB).")

    filename = (file.filename or "").lower()
    try:
        if filename.endswith((".xlsx", ".xls")):
            rows = _parse_content_excel(content)
        elif filename.endswith(".csv"):
            rows = _parse_content_csv(content.decode("utf-8-sig"))
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type. Use .xlsx, .xls, or .csv.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Content file parse error: %s", exc)
        raise HTTPException(status_code=400, detail="Failed to parse file: {}".format(str(exc)))

    # Store source data in content_json alongside existing body_text
    cj = nl.content_json or {}
    if not isinstance(cj, dict):
        cj = {}
    cj["source_data"] = rows
    cj["source_filename"] = file.filename
    nl.content_json = cj
    # Force SQLAlchemy to detect JSON mutation
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(nl, "content_json")
    db.commit()

    return {
        "rows": len(rows),
        "columns": list(rows[0].keys()) if rows else [],
        "filename": file.filename,
        "preview": rows[:3],
    }


def _parse_content_excel(content: bytes) -> list[dict]:
    """Parse Excel file into list of row dicts."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = [str(c or "").strip() for c in next(rows_iter, [])]
    if not header:
        raise ValueError("Empty spreadsheet.")
    result = []
    for row in rows_iter:
        row_dict = {}
        for i, val in enumerate(row):
            if i < len(header) and header[i]:
                row_dict[header[i]] = str(val) if val is not None else ""
        if any(v.strip() for v in row_dict.values()):
            result.append(row_dict)
    wb.close()
    return result


def _parse_content_csv(text: str) -> list[dict]:
    """Parse CSV text into list of row dicts."""
    reader = csv.DictReader(io.StringIO(text))
    result = []
    for row in reader:
        cleaned = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        if any(cleaned.values()):
            result.append(cleaned)
    return result


class ComposeRequest(BaseModel):
    """Request body for AI newsletter composition."""
    prompt: Optional[str] = None  # Custom instructions for the AI
    reference_files: Optional[list[str]] = None  # Additional context text blobs (max 5)

    @field_validator("reference_files")
    @classmethod
    def validate_reference_files(cls, v):
        if v is not None and len(v) > 5:
            raise ValueError("Maximum 5 reference files allowed.")
        return v


class IterateRequest(BaseModel):
    """Request body for iterating on composed content."""
    instructions: str  # What to change/improve


@router.post("/{newsletter_id}/compose")
def compose_newsletter(
    newsletter_id: str,
    body: ComposeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Use AI to compose the newsletter body from uploaded source data.

    Reads: newsletter copy rules, user's StyleProfile, uploaded source_data,
    and optional custom prompt. Calls Claude to generate the body_text.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    cj = nl.content_json or {}
    source_data = cj.get("source_data", [])

    # Load newsletter copy rules
    from .settings import _get_user_setting, NEWSLETTER_COPY_KEY
    copy_rules = _get_user_setting(db, NEWSLETTER_COPY_KEY, current_user.id)

    # Load style profile
    from .style import _get_latest_profile
    from ..services.style_analyzer import build_style_prompt
    style_profile = _get_latest_profile(db, current_user)
    style_block = build_style_prompt(style_profile) if style_profile else ""

    # Build the AI prompt
    prompt_parts = []

    if copy_rules:
        prompt_parts.append("=== NEWSLETTER WRITING STYLE GUIDE ===\n" + copy_rules.strip())

    if style_block:
        prompt_parts.append("=== AUTHOR VOICE PROFILE ===\n" + style_block.strip())

    if source_data:
        import json
        # Truncate to first 50 rows to avoid token explosion
        data_preview = source_data[:50]
        prompt_parts.append(
            "=== SOURCE MATERIAL (from uploaded file: {}) ===\n"
            "Use this data to compose the newsletter. Extract the most interesting and relevant points.\n\n{}".format(
                cj.get("source_filename", "spreadsheet"),
                json.dumps(data_preview, indent=2, default=str),
            )
        )

    if body.reference_files:
        for i, ref in enumerate(body.reference_files):
            if ref and ref.strip():
                prompt_parts.append("=== REFERENCE MATERIAL {} ===\n{}".format(i + 1, ref.strip()[:5000]))

    subject_hint = nl.subject_line or nl.title or ""

    task = (
        "Write a newsletter body for the subject: \"{subject}\"\n\n"
        "Requirements:\n"
        "- Output ONLY the newsletter body text, no subject line, no greeting like 'Dear reader'.\n"
        "- Use {{{{name}}}} placeholder for the reader's first name (e.g. 'Hi {{{{name}}}},' as opening).\n"
        "- Use {{{{company}}}} placeholder if referencing the reader's company.\n"
        "- Write in plain text with paragraph breaks (double newline between paragraphs).\n"
        "- Keep it concise: 200-400 words unless the material demands more.\n"
        "- Follow the writing style guide and voice profile above.\n"
    ).format(subject=subject_hint)

    if body.prompt:
        task += "\nAdditional instructions from the author:\n" + body.prompt.strip()[:2000]

    user_prompt = "\n\n".join(prompt_parts) + "\n\n=== TASK ===\n" + task

    # Call Claude
    import anthropic
    from ..config import MODEL_SMART

    client = anthropic.Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        timeout=60.0,
    )
    try:
        msg = client.messages.create(
            model=MODEL_SMART,
            max_tokens=2000,
            messages=[{"role": "user", "content": user_prompt}],
        )
        generated_text = msg.content[0].text.strip()
    except Exception as exc:
        logger.error("Newsletter compose failed: %s", exc)
        raise HTTPException(status_code=500, detail="AI composition failed. Please try again.")

    # Store the generated body and the conversation for iteration
    cj["body_text"] = generated_text
    cj["compose_history"] = [
        {"role": "user", "content": user_prompt},
        {"role": "assistant", "content": generated_text},
    ]
    nl.content_json = cj
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(nl, "content_json")
    db.commit()

    return {
        "body_text": generated_text,
        "tokens_input": msg.usage.input_tokens,
        "tokens_output": msg.usage.output_tokens,
    }


@router.post("/{newsletter_id}/iterate")
def iterate_newsletter(
    newsletter_id: str,
    body: IterateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Iterate on the AI-composed newsletter body with additional instructions.

    Sends the full conversation history + new instructions to Claude for refinement.
    """
    nl = _get_newsletter_or_404(db, newsletter_id, current_user)

    cj = nl.content_json or {}
    history = cj.get("compose_history", [])
    if not history:
        raise HTTPException(
            status_code=400,
            detail="No composition history found. Use /compose first.",
        )

    # Add the iteration instruction
    iteration_prompt = (
        "Please revise the newsletter body based on these instructions:\n\n"
        + body.instructions.strip()[:2000]
        + "\n\nOutput ONLY the revised newsletter body text. No explanations."
    )
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": iteration_prompt})

    # Call Claude with full conversation
    import anthropic
    from ..config import MODEL_SMART

    client = anthropic.Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        timeout=60.0,
    )
    try:
        msg = client.messages.create(
            model=MODEL_SMART,
            max_tokens=2000,
            messages=messages,
        )
        revised_text = msg.content[0].text.strip()
    except Exception as exc:
        logger.error("Newsletter iterate failed: %s", exc)
        raise HTTPException(status_code=500, detail="AI iteration failed. Please try again.")

    # Update stored body and history
    cj["body_text"] = revised_text
    history.append({"role": "user", "content": iteration_prompt})
    history.append({"role": "assistant", "content": revised_text})
    # Keep history from growing unbounded — keep last 10 turns
    if len(history) > 10:
        history = history[-10:]
    cj["compose_history"] = history
    nl.content_json = cj
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(nl, "content_json")
    db.commit()

    return {
        "body_text": revised_text,
        "tokens_input": msg.usage.input_tokens,
        "tokens_output": msg.usage.output_tokens,
    }
