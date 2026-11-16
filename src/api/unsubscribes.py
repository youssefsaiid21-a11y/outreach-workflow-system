"""
Campaign-level suppression list management — add, remove, import, and check unsubscribes.

This module manages the UnsubscribeEntry table, which is the campaign/outreach
suppression list (distinct from NewsletterUnsubscribe which handles newsletter-specific
unsubscribes). When an email is added to this list:

  1. The UnsubscribeEntry row is created (scoped to the current user)
  2. All matching Prospect rows owned by that user are set to status=unsubscribed
  3. All active EmailSequences for those prospects are set to status=unsubscribed

Suppression is checked at multiple points in the pipeline:
  - Pre-launch campaign validation (suppression.py filter_prospects_for_campaign)
  - Newsletter recipient population and send (newsletters.py, newsletter_sender.py)
  - Orchestration step execution (before each email send)

Scoping model:
  - Each UnsubscribeEntry has a user_id (the rep who suppressed it)
  - Global entries (user_id=NULL) apply to all users (legacy/admin entries)
  - User-scoped entries only affect that user's prospects
  - Admins can delete global entries; non-admins can only delete their own

Import supports CSV and Excel files — scans every cell for email addresses
(no specific column required). Handles BOM-encoded CSVs and openpyxl for Excel.
File size capped at 10MB to prevent OOM attacks.
"""

import csv
import io
import logging
import re
from typing import Optional
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..db.database import get_db
from ..db.models import UnsubscribeEntry, Prospect, EmailSequence, ProspectStatus, SequenceStatus, User, UserRole
from .auth import get_current_user

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

router = APIRouter(prefix="/unsubscribes", tags=["unsubscribes"])
logger = logging.getLogger(__name__)


class UnsubscribeRequest(BaseModel):
    email: str
    reason: Optional[str] = "manual"
    source: Optional[str] = "manual"


@router.get("/")
def list_unsubscribes(
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List suppressed emails for the current user (+ global entries)."""
    q = db.query(UnsubscribeEntry).filter(
        (UnsubscribeEntry.user_id == current_user.id) | (UnsubscribeEntry.user_id.is_(None))
    ).order_by(UnsubscribeEntry.created_at.desc())
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    return {
        "items": [
            {
                "email": r.email,
                "reason": r.reason,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/import")
async def import_suppression_file(file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Bulk-import a suppression list from a CSV or Excel file.
    Scans every cell/field for valid email addresses  -  no specific column required.
    Returns counts of newly added vs already-suppressed emails.
    """
    # SWAT-H10: limit file upload size to prevent OOM
    _MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
    filename = (file.filename or "").lower()
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large. Maximum size is {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")

    emails: list[str] = []

    if filename.endswith(".csv") or file.content_type in ("text/csv", "text/plain"):
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            for cell in row:
                emails += _EMAIL_RE.findall(cell)

    elif filename.endswith((".xlsx", ".xls")):
        try:
            import openpyxl
        except ImportError:
            raise HTTPException(400, "openpyxl is required to parse Excel files. Run: pip install openpyxl")
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for cell in row:
                    if cell is not None:
                        emails += _EMAIL_RE.findall(str(cell))
        wb.close()

    else:
        raise HTTPException(400, "Unsupported file type. Upload a .csv or .xlsx file.")

    if not emails:
        raise HTTPException(422, "No email addresses found in the file.")

    # Deduplicate and normalize
    unique_emails = list({e.strip().lower() for e in emails})

    # Batch-check which emails are already suppressed (single query)
    existing_suppressed = set()
    for i in range(0, len(unique_emails), 500):
        batch = unique_emails[i:i + 500]
        rows = db.query(UnsubscribeEntry.email).filter(UnsubscribeEntry.email.in_(batch)).all()
        existing_suppressed.update(r.email for r in rows)

    new_emails = [e for e in unique_emails if e not in existing_suppressed]
    skipped = len(unique_emails) - len(new_emails)

    # Bulk insert new suppression entries
    for email in new_emails:
        db.add(UnsubscribeEntry(email=email, reason="imported", source="file_import", user_id=current_user.id))

    # FIX-32: Look up the entries we just created (or that already existed) to
    # see whether any are user-scoped, then only update matching prospects.
    if new_emails:
        # Re-query freshly so we see the user_id stamped on each entry.
        entry_user_map: dict[str, str | None] = {
            r.email: r.user_id
            for r in db.query(UnsubscribeEntry).filter(UnsubscribeEntry.email.in_(new_emails)).all()
        }
        matching_prospects = db.query(Prospect).filter(Prospect.email.in_(new_emails)).all()
        prospect_ids = []
        for p in matching_prospects:
            entry_uid = entry_user_map.get(p.email)
            # Global entry (no user) → suppress everywhere (back-compat).
            # User-scoped entry → only suppress prospects owned by that user.
            if entry_uid is not None and p.user_id != entry_uid:
                continue
            p.status = ProspectStatus.unsubscribed
            prospect_ids.append(p.id)

        if prospect_ids:
            _terminal = (SequenceStatus.replied, SequenceStatus.bounced, SequenceStatus.unsubscribed, SequenceStatus.connected, SequenceStatus.messaged)
            active_seqs = (
                db.query(EmailSequence)
                .filter(
                    EmailSequence.prospect_id.in_(prospect_ids),
                    ~EmailSequence.status.in_(_terminal),
                )
                .all()
            )
            for seq in active_seqs:
                seq.status = SequenceStatus.unsubscribed

    added = len(new_emails)
    db.commit()
    logger.info(f"Suppression import: {added} added, {skipped} already suppressed")
    return {"added": added, "skipped": skipped, "total_found": len(emails)}


@router.post("/")
def add_unsubscribe(req: UnsubscribeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Add an email to the suppression list and mark matching prospect(s) as unsubscribed."""
    email = req.email.strip().lower()
    if not email:
        raise HTTPException(400, "Email is required.")
    if not _EMAIL_RE.fullmatch(email):
        raise HTTPException(400, "Invalid email format.")

    # Idempotent  -  do nothing if already suppressed for this user
    existing = db.query(UnsubscribeEntry).filter_by(email=email, user_id=current_user.id).first()
    if not existing:
        db.add(UnsubscribeEntry(email=email, reason=req.reason, source=req.source, user_id=current_user.id))

    # Mark only this user's matching prospects as unsubscribed
    prospects = db.query(Prospect).filter(Prospect.email.ilike(email), Prospect.user_id == current_user.id).all()
    for p in prospects:
        p.status = ProspectStatus.unsubscribed
        # Mark their active sequences too
        for seq in db.query(EmailSequence).filter_by(prospect_id=p.id).all():
            if seq.status not in (SequenceStatus.replied, SequenceStatus.bounced, SequenceStatus.unsubscribed, SequenceStatus.connected, SequenceStatus.messaged):
                seq.status = SequenceStatus.unsubscribed

    db.commit()
    return {"suppressed": email, "prospects_updated": len(prospects)}


@router.delete("/{email}")
def remove_unsubscribe(email: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Remove an email from the current user's suppression list."""
    email = email.strip().lower()
    # SWAT-H11: only allow deleting own entries; admins can delete global entries
    row = db.query(UnsubscribeEntry).filter_by(email=email, user_id=current_user.id).first()
    if not row and current_user.role == UserRole.admin:
        row = db.query(UnsubscribeEntry).filter_by(email=email, user_id=None).first()
    if not row:
        raise HTTPException(404, "Email not found in your suppression list.")
    db.delete(row)
    db.commit()
    return {"removed": email}


def is_suppressed(email: str, db: Session) -> bool:
    """Check whether an email is on the campaign-level suppression list.

    NOTE: This is a simple check that ignores user scoping — it returns True
    if ANY user has suppressed this email. For user-scoped checks, use
    suppression.py's is_prospect_blocked() which respects the user_id filter.
    Used as a quick gate during campaign launch validation.
    """
    return db.query(UnsubscribeEntry).filter_by(email=email.strip().lower()).first() is not None
