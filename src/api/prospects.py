"""
Prospect management API — search, import, validate, enrich, and manage outreach contacts.

This is the lead management hub of the outreach system. Prospects are the people
we're reaching out to — HSE directors, safety managers, etc. in the oil & gas,
construction, and mining industries.

Prospect lifecycle:
  1. **Discovery**: Search Apollo.io by title/industry/location → reveal emails → save
  2. **Import**: CSV upload or Apollo search, auto-deduped per user, auto-grouped by Account
  3. **Validation**: Email addresses verified via Reacher (self-hosted). Small batches
     run synchronously; large batches spawn a background Job with per-email checkpointing
  4. **Recovery**: For invalid/bounced emails, attempts to find alternatives via Hunter.io
     pattern matching, then re-validates with Reacher
  5. **Enrichment**: LinkedIn profiles, company descriptions via background Job
  6. **Outreach**: Assigned to campaigns, sequences generated, emails sent

Key data relationships:
  - Prospect → AudienceSegment (optional grouping by search criteria)
  - Prospect → Account (auto-created from email domain, groups contacts at same company)
  - Prospect → EmailSequence (1:many, one per campaign enrolled in)
  - Prospect → Reply (1:many, tracked replies)

Security (FIX-6):
  Non-admin users are always scoped to their own prospects. Admins can view/manage
  across users. Delete returns 404 (not 403) to prevent existence leaking. Bulk
  operations (clear, assign) require admin or confirmation flags.

Deduplication (FIX-30):
  Prospects are unique per (email, user_id) — different reps can each have the same
  lead. Apollo search and CSV import both dedupe against existing DB records AND
  within the incoming batch (Apollo often returns duplicate records with different IDs).
  Uses savepoints for per-row IntegrityError handling so one duplicate doesn't
  rollback the entire batch.
"""

import uuid
import json
import hashlib
import logging
import csv
import io
import os
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, case, or_
from sqlalchemy.exc import IntegrityError as _IntegrityError
from sqlalchemy.orm import Session, joinedload
from ..db.database import get_db
from ..db.models import Prospect, AudienceSegment, ProspectStatus, EmailSequence, Reply, ReplyStatus, Account, Campaign
from ..services.apollo import ApolloClient
from ..services.reacher import ReacherClient
from ..services.email_recovery import recover_email
from ..config import INVALID_EMAIL_STATUSES, TERMINAL_PROSPECT_STATUSES
from ..db.models import User, LinkedInAccount
from .auth import get_current_user

router = APIRouter(prefix="/prospects", tags=["prospects"])
logger = logging.getLogger(__name__)

_VALID_PROSPECT_STATUSES = {s.value for s in ProspectStatus}

# Free/personal email domains — prospects with these domains are NOT auto-grouped
# into Accounts because they don't represent a company. A prospect with
# john@gmail.com shouldn't be grouped with jane@gmail.com.
_GENERIC_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "mail.com", "protonmail.com", "zoho.com", "yandex.com",
    "live.com", "msn.com", "me.com", "gmx.com", "inbox.com",
}


class ProspectSearchRequest(BaseModel):
    titles: Optional[list[str]] = None
    seniorities: Optional[list[str]] = None
    industries: Optional[list[str]] = None
    employee_ranges: Optional[list[str]] = None
    locations: Optional[list[str]] = None
    limit: int = 50
    save_as_segment: Optional[str] = None


class SegmentCreateRequest(BaseModel):
    name: str
    filters: dict


class AssignRequest(BaseModel):
    prospect_ids: list[str]
    user_id: str


class ValidateRequest(BaseModel):
    prospect_ids: Optional[list[str]] = None
    only_unvalidated: bool = True


class EnrichRequest(BaseModel):
    prospect_ids: Optional[list[str]] = None
    all: bool = False


class RecoverRequest(BaseModel):
    prospect_ids: Optional[list[str]] = None


@router.post("/search")
def search_prospects(req: ProspectSearchRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    has_filter = any([req.titles, req.seniorities, req.industries, req.employee_ranges, req.locations])
    if not has_filter:
        raise HTTPException(
            status_code=422,
            detail="At least one search filter is required (titles, seniorities, industries, employee_ranges, or locations).",
        )
    # Pre-load existing Apollo IDs so the search can skip contacts we
    # already have BEFORE spending reveal credits on them.
    _existing_ids_q = db.query(Prospect.apollo_id).filter(
        Prospect.apollo_id.isnot(None)
    )
    if current_user:
        _existing_ids_q = _existing_ids_q.filter(Prospect.user_id == current_user.id)
    _existing_apollo_ids = {r[0] for r in _existing_ids_q.all() if r[0]}

    apollo = ApolloClient()
    try:
        raw_prospects = apollo.search_all_pages(
            titles=req.titles,
            seniorities=req.seniorities,
            industries=req.industries,
            employee_ranges=req.employee_ranges,
            locations=req.locations,
            limit=req.limit,
            existing_apollo_ids=_existing_apollo_ids,
            db=db,
        )
    finally:
        apollo.close()

    # FIX-30 + Audit 2026-05-05: dedupe per user AND within the batch.
    # Apollo often returns multiple records with different IDs but the
    # same email. Without within-batch dedup, duplicates survive the DB
    # check but then hit IntegrityError on flush — and db.rollback()
    # wipes ALL pending inserts, not just the duplicate.
    incoming_emails = [p["email"].lower() for p in raw_prospects if p.get("email")]
    existing_q = db.query(Prospect.email).filter(
        func.lower(Prospect.email).in_(incoming_emails)
    )
    if current_user:
        existing_q = existing_q.filter(Prospect.user_id == current_user.id)
    existing_emails = {row[0].lower() for row in existing_q.all()}

    # Within-batch dedup: only keep the first occurrence of each email
    seen_emails = set(existing_emails)
    new_prospects = []
    for p in raw_prospects:
        email = (p.get("email") or "").lower()
        if not email:
            continue
        if email in seen_emails:
            continue
        seen_emails.add(email)
        new_prospects.append(p)
    skipped = len(raw_prospects) - len(new_prospects)

    segment_id = None
    if req.save_as_segment:
        existing = db.query(AudienceSegment).filter_by(name=req.save_as_segment).first()
        if existing:
            existing.filters = {
                "titles": req.titles,
                "seniorities": req.seniorities,
                "industries": req.industries,
                "employee_ranges": req.employee_ranges,
                "locations": req.locations,
            }
            segment_id = existing.id
        else:
            seg = AudienceSegment(
                id=str(uuid.uuid4()),
                name=req.save_as_segment,
                filters={
                    "titles": req.titles,
                    "seniorities": req.seniorities,
                    "industries": req.industries,
                    "employee_ranges": req.employee_ranges,
                    "locations": req.locations,
                },
            )
            db.add(seg)
            db.flush()
            segment_id = seg.id

    saved = []
    for p in new_prospects:
        account_id = _ensure_account(
            p.get("email", ""), p.get("company", ""),
            p.get("industry", ""), p.get("headcount"), db,
        )
        prospect = Prospect(
            id=str(uuid.uuid4()),
            user_id=current_user.id if current_user else None,
            apollo_id=p.get("apollo_id"),
            first_name=p["first_name"],
            last_name=p.get("last_name", ""),
            email=p["email"],
            title=p.get("title", ""),
            company=p.get("company", ""),
            industry=p.get("industry", ""),
            headcount=p.get("headcount"),
            location=p.get("location", ""),
            linkedin_url=p.get("linkedin_url", ""),
            company_website=p.get("company_website", ""),
            segment_id=segment_id,
            account_id=account_id,
            status=ProspectStatus.new,
        )
        # Audit 2026-05-05: use a savepoint so a duplicate IntegrityError
        # only rolls back THIS insert, not all previously flushed prospects.
        # Without this, the first duplicate wipes every prospect saved so
        # far in this batch — the root cause of data loss.
        nested = db.begin_nested()
        try:
            db.add(prospect)
            db.flush()
            saved.append(prospect)
        except _IntegrityError:
            nested.rollback()
            logger.warning("Skipped duplicate prospect email: %s", p["email"])
            skipped += 1

    db.commit()

    return {
        "found": len(raw_prospects),
        "saved": len(saved),
        "skipped_duplicates": skipped,
        "segment_id": segment_id,
        "prospects": [_prospect_to_dict(p) for p in saved],
    }


@router.get("/")
def list_prospects(
    status: Optional[str] = None,
    segment_id: Optional[str] = None,
    user_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Validate status before querying  -  invalid values would silently return empty results.
    if status and status not in _VALID_PROSPECT_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Valid values: {sorted(_VALID_PROSPECT_STATUSES)}"
        )

    q = db.query(Prospect).options(joinedload(Prospect.segment))
    # FIX-6: non-admins are forced to their own scope regardless of query
    # param. Admins may pass an explicit ``user_id`` to inspect another rep.
    is_admin = bool(current_user and current_user.role and current_user.role.value == "admin")
    if current_user and not is_admin:
        q = q.filter(Prospect.user_id == current_user.id)
    elif user_id:
        q = q.filter(Prospect.user_id == user_id)
    if status:
        q = q.filter(Prospect.status == status)
    if segment_id:
        q = q.filter(Prospect.segment_id == segment_id)
    if search:
        escaped = search.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        q = q.filter(
            Prospect.email.ilike(pattern)
            | Prospect.first_name.ilike(pattern)
            | Prospect.last_name.ilike(pattern)
            | Prospect.company.ilike(pattern)
        )
    total = q.count()
    prospects = q.order_by(Prospect.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "prospects": [_prospect_to_dict(p) for p in prospects],
    }


@router.post("/assign")
def assign_prospects(
    req: AssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Assign a batch of prospects to a specific user (rep). FIX-6: admin only."""
    is_admin = bool(current_user and current_user.role and current_user.role.value == "admin")
    if current_user and not is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required.")

    user = db.query(User).filter_by(id=req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{req.user_id}' not found.")
    if not req.prospect_ids:
        raise HTTPException(status_code=422, detail="prospect_ids must not be empty.")

    updated = (
        db.query(Prospect)
        .filter(Prospect.id.in_(req.prospect_ids))
        .update({Prospect.user_id: req.user_id}, synchronize_session=False)
    )
    db.commit()
    return {"assigned": updated, "user_id": req.user_id, "user_name": user.name}


@router.get("/export")
def export_prospects_csv(
    status: Optional[str] = None,
    segment_id: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export prospects as CSV. Respects the same filters as the list endpoint."""
    if status and status not in _VALID_PROSPECT_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Valid values: {sorted(_VALID_PROSPECT_STATUSES)}"
        )
    q = db.query(Prospect).options(joinedload(Prospect.segment))
    # FIX-6: scope export to current user (non-admin).
    is_admin = bool(current_user and current_user.role and current_user.role.value == "admin")
    if current_user and not is_admin:
        q = q.filter(Prospect.user_id == current_user.id)
    if status:
        q = q.filter(Prospect.status == status)
    if segment_id:
        q = q.filter(Prospect.segment_id == segment_id)
    if search:
        escaped = search.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        q = q.filter(
            Prospect.email.ilike(pattern)
            | Prospect.first_name.ilike(pattern)
            | Prospect.last_name.ilike(pattern)
            | Prospect.company.ilike(pattern)
        )
    _EXPORT_LIMIT = 50000  # safety cap to prevent memory exhaustion
    _BATCH_SIZE = 500

    fieldnames = [
        "id", "first_name", "last_name", "email", "title",
        "company", "industry", "headcount", "location",
        "linkedin_url", "company_website", "status",
    ]

    def _generate_csv():
        # Yield header
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        yield output.getvalue().encode()
        # Stream rows from DB in batches to avoid loading all into memory
        batch_q = q.order_by(Prospect.created_at.desc()).limit(_EXPORT_LIMIT).yield_per(_BATCH_SIZE)
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        count = 0
        for p in batch_q:
            writer.writerow({k: v for k, v in _prospect_to_dict(p).items()
                             if k in fieldnames})
            count += 1
            if count % _BATCH_SIZE == 0:
                yield output.getvalue().encode()
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=fieldnames)
        # Flush remaining rows
        remaining = output.getvalue()
        if remaining:
            yield remaining.encode()

    return StreamingResponse(
        _generate_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=prospects.csv"},
    )


@router.post("/import")
async def import_prospects_csv(
    file: UploadFile = File(...),
    segment_name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Import prospects from a CSV file.
    Required column: email. Optional: first_name, last_name, title, company,
    industry, headcount, location, linkedin_url, company_website.
    Returns {imported, skipped_duplicates, errors, segment_id}.
    """
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # handle BOM
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))

    # Normalise header names: strip whitespace, lowercase
    def _col(row: dict, *names: str) -> str:
        for n in names:
            v = row.get(n) or row.get(n.lower()) or row.get(n.upper()) or ""
            if v:
                return v.strip()
        return ""

    # Collect all rows first to de-dup against DB in one query
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV file is empty.")

    # Normalise header keys once
    norm_rows = [{k.strip().lower(): (v or "").strip() for k, v in r.items()} for r in rows]

    emails = [r.get("email", "") for r in norm_rows if r.get("email")]
    if not emails:
        raise HTTPException(status_code=400, detail="No 'email' column found or all emails are blank.")

    # FIX-30: dedupe per user  -  different reps can each import the same lead.
    existing_q = db.query(Prospect.email).filter(
        func.lower(Prospect.email).in_([e.lower() for e in emails])
    )
    if current_user:
        existing_q = existing_q.filter(Prospect.user_id == current_user.id)
    existing = {row[0].lower() for row in existing_q.all()}

    # Optional: create / find segment
    segment = None
    if segment_name and segment_name.strip():
        segment = db.query(AudienceSegment).filter_by(name=segment_name.strip()).first()
        if not segment:
            segment = AudienceSegment(
                id=str(uuid.uuid4()),
                name=segment_name.strip(),
                filters={},
            )
            db.add(segment)
            db.flush()

    imported = 0
    skipped = 0
    errors = 0

    for r in norm_rows:
        email = r.get("email", "").strip()
        if not email or "@" not in email:
            errors += 1
            continue
        if email.lower() in existing:
            skipped += 1
            continue
        _company = r.get("company") or r.get("company_name") or ""
        _industry = r.get("industry") or ""
        _headcount = int(r["headcount"]) if r.get("headcount", "").isdigit() else None
        account_id = _ensure_account(email, _company, _industry, _headcount, db)
        p = Prospect(
            id=str(uuid.uuid4()),
            user_id=current_user.id if current_user else None,
            email=email,
            first_name=r.get("first_name") or r.get("firstname") or "",
            last_name=r.get("last_name") or r.get("lastname") or "",
            title=r.get("title") or r.get("job_title") or "",
            company=_company,
            industry=_industry,
            headcount=_headcount,
            location=r.get("location") or r.get("city") or "",
            linkedin_url=r.get("linkedin_url") or r.get("linkedin") or "",
            company_website=r.get("company_website") or r.get("website") or "",
            segment_id=segment.id if segment else None,
            account_id=account_id,
            status=ProspectStatus.new,
        )
        db.add(p)
        try:
            db.flush()
            existing.add(email.lower())
            imported += 1
        except _IntegrityError:
            db.rollback()
            logger.debug("Skipped duplicate prospect on import: %s", email)
            skipped += 1

    # prospect_count is computed dynamically  -  no static counter to update

    db.commit()
    return {
        "imported": imported,
        "skipped_duplicates": skipped,
        "errors": errors,
        "segment_id": segment.id if segment else None,
    }


@router.get("/segments")
def list_segments(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    segments = db.query(AudienceSegment).all()
    # Compute prospect counts scoped to current user
    seg_counts = dict(
        db.query(Prospect.segment_id, func.count(Prospect.id))
        .filter(Prospect.segment_id.isnot(None), Prospect.user_id == current_user.id)
        .group_by(Prospect.segment_id)
        .all()
    )
    return [
        {
            "id": s.id,
            "name": s.name,
            "filters": s.filters,
            "prospect_count": seg_counts.get(s.id, 0),
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in segments
    ]


@router.post("/segments")
def create_segment(req: SegmentCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    existing = db.query(AudienceSegment).filter_by(name=req.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Segment '{req.name}' already exists.")
    seg = AudienceSegment(
        id=str(uuid.uuid4()),
        name=req.name,
        filters=req.filters,
    )
    db.add(seg)
    db.commit()
    return {"id": seg.id, "name": seg.name}


@router.delete("/segments/{segment_id}")
def delete_segment(segment_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    seg = db.query(AudienceSegment).filter_by(id=segment_id).first()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found.")
    db.delete(seg)
    db.commit()
    return {"deleted": segment_id}


@router.delete("/clear")
def clear_prospects(
    segment_id: Optional[str] = None,
    confirm: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete prospects. Requires confirm=true to prevent accidental mass deletes.
    Pass segment_id to scope the delete to a segment; omit to delete all prospects.

    FIX-6: scoped to the current user  -  calling this no longer wipes every
    rep's prospects.
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass confirm=true to confirm deletion. This cannot be undone."
        )
    q = db.query(Prospect)
    if current_user:
        q = q.filter(Prospect.user_id == current_user.id)
    if segment_id:
        seg = db.query(AudienceSegment).filter_by(id=segment_id).first()
        if not seg:
            raise HTTPException(status_code=404, detail="Segment not found.")
        q = q.filter(Prospect.segment_id == segment_id)

    # Collect prospect IDs to delete dependent records first (FK cascade not reliable in SQLite bulk delete)
    prospect_ids = [p.id for p in q.with_entities(Prospect.id).all()]
    if prospect_ids:
        db.query(EmailSequence).filter(EmailSequence.prospect_id.in_(prospect_ids)).delete(synchronize_session=False)
        db.query(Reply).filter(Reply.prospect_id.in_(prospect_ids)).delete(synchronize_session=False)
        # Clean up orchestration states if the table exists
        try:
            from ..orchestration.orm import OrchSequenceStateORM
            db.query(OrchSequenceStateORM).filter(OrchSequenceStateORM.lead_id.in_(prospect_ids)).delete(synchronize_session=False)
        except Exception:
            pass
    deleted = q.delete(synchronize_session=False)
    db.commit()
    logger.warning(f"Bulk delete: {deleted} prospects removed (segment_id={segment_id})")
    return {"deleted": deleted}


@router.delete("/{prospect_id}")
def delete_prospect(
    prospect_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = db.query(Prospect).filter_by(id=prospect_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Prospect not found.")
    # FIX-6: ownership check  -  return 404 (not 403) to avoid leaking existence.
    if current_user and p.user_id and p.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Prospect not found.")
    # Delete dependent records first (FK cascade not reliable in SQLite bulk ops)
    db.query(EmailSequence).filter_by(prospect_id=prospect_id).delete(synchronize_session=False)
    db.query(Reply).filter_by(prospect_id=prospect_id).delete(synchronize_session=False)
    try:
        from ..orchestration.orm import OrchSequenceStateORM
        db.query(OrchSequenceStateORM).filter_by(lead_id=prospect_id).delete(synchronize_session=False)
    except Exception:
        pass
    db.delete(p)
    db.commit()
    return {"deleted": prospect_id}


@router.post("/validate")
def validate_prospects(
    req: ValidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Validates email addresses via Reacher (self-hosted).

    For small batches (≤10 emails): runs synchronously and returns results.
    For large batches (>10 emails): spawns a background job and returns a
    job_id that the browser can poll via GET /api/jobs/{job_id}.

    Each email is committed to the DB individually so progress survives crashes.
    """
    import threading
    from ..db.database import SessionLocal
    from ..db.models import Job

    rc = ReacherClient()
    if not rc.is_healthy():
        raise HTTPException(status_code=400, detail="Reacher is unreachable. Check the VPS.")

    q = db.query(Prospect)
    # FIX-6: scope to current user.
    if current_user:
        q = q.filter(Prospect.user_id == current_user.id)
    if req.prospect_ids:
        q = q.filter(Prospect.id.in_(req.prospect_ids))
    elif req.only_unvalidated:
        q = q.filter(Prospect.email_validation_status.is_(None))

    prospects = q.all()

    if not prospects:
        return {"validated": 0, "message": "No prospects to validate."}

    # Small batch  -  run synchronously (fast enough for the HTTP request)
    if len(prospects) <= 10:
        return _validate_sync(prospects, rc, db)

    # Large batch  -  run as a background job with per-email checkpointing
    # Prevent duplicate jobs
    existing = db.query(Job).filter(
        Job.type == "validate_emails",
        Job.status.in_(["pending", "running"]),
    ).first()
    if existing:
        return {"job_id": existing.id, "message": "Validation already in progress."}

    prospect_ids = [p.id for p in prospects]
    job = Job(
        id=str(uuid.uuid4()),
        type="validate_emails",
        status="pending",
        payload={"prospect_ids": prospect_ids},
        progress=0,
        progress_detail=f"Queued  -  {len(prospect_ids)} emails to validate",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    thread = threading.Thread(
        target=_run_validate_job,
        args=(job.id, prospect_ids),
        name=f"validate-{job.id[:8]}",
        daemon=True,
    )
    thread.start()

    return {"job_id": job.id, "total": len(prospect_ids), "message": "Validation started in background."}


def _validate_sync(prospects: list, rc, db) -> dict:
    """Synchronous validation for small batches (≤10). Commits per email."""
    stats = {"valid": 0, "invalid": 0, "catch_all": 0, "unknown": 0, "other": 0}

    for prospect in prospects:
        result = rc.validate_single(prospect.email)
        prospect.email_validation_status = result["status"]
        prospect.email_validation_sub_status = result.get("sub_status", "")
        prospect.email_validated_at = datetime.now(timezone.utc)
        db.commit()  # checkpoint  -  survives crash

        status_key = result["status"].replace("-", "_")
        stats[status_key] = stats.get(status_key, 0) + 1

    return {"validated": len(prospects), "stats": stats}


def _run_validate_job(job_id: str, prospect_ids: list[str]):
    """Background thread: validates emails with per-email checkpointing."""
    from ..db.database import SessionLocal
    from ..db.models import Job

    db = SessionLocal()
    try:
        job = db.query(Job).filter_by(id=job_id).first()
        if not job:
            return
        job.status = "running"
        job.progress_detail = "Starting validation..."
        db.commit()

        rc = ReacherClient()
        total = len(prospect_ids)
        validated = 0
        stats = {"valid": 0, "invalid": 0, "catch_all": 0, "unknown": 0, "other": 0}

        # Build email list from prospect IDs
        prospects = db.query(Prospect).filter(Prospect.id.in_(prospect_ids)).all()
        email_to_prospect = {p.email.lower(): p for p in prospects}
        emails = [p.email for p in prospects]

        def on_result(email: str, result: dict):
            """Checkpoint callback  -  saves each result to DB immediately."""
            nonlocal validated
            prospect = email_to_prospect.get(email.lower())
            if not prospect:
                return
            prospect.email_validation_status = result["status"]
            prospect.email_validation_sub_status = result.get("sub_status", "")
            prospect.email_validated_at = datetime.now(timezone.utc)
            validated += 1

            status_key = result["status"].replace("-", "_")
            stats[status_key] = stats.get(status_key, 0) + 1

            # Update job progress
            job.progress = int((validated / total) * 100)
            job.progress_detail = f"Validated {validated}/{total}  -  valid: {stats['valid']}, invalid: {stats['invalid']}, catch_all: {stats['catch_all']}, unknown: {stats['unknown']}"
            db.commit()  # checkpoint per email

            # Check if job was cancelled
            db.refresh(job)
            if job.status == "cancelled":
                raise _JobCancelled()

        try:
            rc.validate_batch(emails, on_result=on_result)
        except _JobCancelled:
            logger.info("Validate job %s cancelled by user after %d/%d", job_id, validated, total)
            job.progress_detail = f"Cancelled after {validated}/{total} emails."
            job.result = stats
            db.commit()
            return

        # Done
        job.status = "done"
        job.progress = 100
        job.progress_detail = f"Done  -  {validated} emails validated."
        job.result = stats
        db.commit()
        logger.info("Validate job %s complete: %d emails, stats=%s", job_id, validated, stats)

    except Exception as e:
        logger.error("Validate job %s crashed: %s", job_id, e, exc_info=True)
        try:
            job = db.query(Job).filter_by(id=job_id).first()
            if job:
                job.status = "failed"
                job.error = str(e)
                job.progress_detail = f"Failed after {validated}/{total}: {e}"
                job.result = stats
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


class _JobCancelled(Exception):
    """Raised inside on_result callback when the user cancels the job."""
    pass


@router.get("/validate/credits")
def get_validation_credits():
    """Returns unlimited (-1) since Reacher is self-hosted."""
    return {"credits": -1, "unlimited": True, "provider": "reacher"}


@router.post("/recover-emails")
def recover_invalid_emails(
    req: RecoverRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    For each prospect whose email is invalid/bounced, attempt to find a working
    replacement via Hunter.io and pattern inference, then validate with Reacher.

    FIX-6: scoped to the current user.
    """
    q = db.query(Prospect)
    if current_user:
        q = q.filter(Prospect.user_id == current_user.id)
    if req.prospect_ids:
        q = q.filter(
            Prospect.id.in_(req.prospect_ids),
            Prospect.email_validation_status.in_(list(INVALID_EMAIL_STATUSES)),
        )
    else:
        q = q.filter(Prospect.email_validation_status.in_(list(INVALID_EMAIL_STATUSES)))

    prospects = q.all()
    if not prospects:
        return {"message": "No invalid/bounced prospects found.", "recovered": 0, "failed": 0}

    rc = ReacherClient()

    hunter_key = os.getenv("HUNTER_API_KEY", "")
    recovered, failed, skipped = 0, 0, 0
    details = []

    try:
        for p in prospects:
            try:
                new_email = recover_email(p, db, hunter_key, rc)
                if new_email:
                    old_email = p.email
                    p.email = new_email
                    p.email_validation_status = "valid"
                    p.email_validation_sub_status = "recovered"
                    p.email_validated_at = datetime.now(timezone.utc)
                    # Only reset status if not already in a terminal state
                    if not p.status or p.status.value not in TERMINAL_PROSPECT_STATUSES:
                        p.status = ProspectStatus.pending
                    recovered += 1
                    details.append({
                        "id": p.id,
                        "name": f"{p.first_name} {p.last_name or ''}".strip(),
                        "old_email": old_email,
                        "new_email": new_email,
                        "result": "recovered",
                    })
                    logger.info(f"Recovered email for {p.first_name} {p.last_name}: {old_email} → {new_email}")
                else:
                    skipped += 1
                    details.append({
                        "id": p.id,
                        "name": f"{p.first_name} {p.last_name or ''}".strip(),
                        "old_email": p.email,
                        "new_email": None,
                        "result": "not_found",
                    })
            except Exception as e:
                logger.error(f"Recovery failed for {p.email}: {e}")
                failed += 1
                details.append({
                    "id": p.id,
                    "name": f"{p.first_name} {p.last_name or ''}".strip(),
                    "old_email": p.email,
                    "new_email": None,
                    "result": f"error: {e}",
                })
        # Batch commit all recovery changes at once
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        rc.close()

    return {
        "total": len(prospects),
        "recovered": recovered,
        "not_found": skipped,
        "failed": failed,
        "details": details,
    }


def _ensure_account(
    prospect_email: str,
    prospect_company: str,
    prospect_industry: str,
    prospect_headcount: int | None,
    db: Session,
) -> str | None:
    """Auto-create or find Account by email domain. Returns account_id.

    Accounts group prospects from the same company (by email domain).
    This enables per-company engagement tracking in the accounts view.
    Skips generic email domains (gmail.com, etc.) — those aren't real companies.
    Uses a flush + IntegrityError catch for race-condition safety when
    two prospects with the same domain are inserted concurrently.
    """
    from sqlalchemy.exc import IntegrityError

    if not prospect_email or "@" not in prospect_email:
        return None
    domain = prospect_email.split("@", 1)[1].lower().strip()
    if domain in _GENERIC_DOMAINS:
        return None

    account = db.query(Account).filter_by(domain=domain).first()
    if account:
        return account.id

    account = Account(
        id=str(uuid.uuid4()),
        domain=domain,
        name=prospect_company or None,
        industry=prospect_industry or None,
        employee_count=prospect_headcount,
    )
    db.add(account)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        account = db.query(Account).filter_by(domain=domain).first()
        if account:
            return account.id
        return None
    return account.id


@router.post("/accounts/sync")
def sync_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Iterate all prospects without an account_id and assign them to accounts.

    FIX-6: scoped to current user.
    """
    q = db.query(Prospect).filter(Prospect.account_id.is_(None))
    if current_user:
        q = q.filter(Prospect.user_id == current_user.id)
    prospects = q.all()
    if not prospects:
        return {"synced": 0, "accounts_created": 0}

    synced = 0
    accounts_created_before = db.query(func.count(Account.id)).scalar() or 0
    for p in prospects:
        aid = _ensure_account(p.email, p.company, p.industry, p.headcount, db)
        if aid:
            p.account_id = aid
            synced += 1
    db.commit()
    accounts_created_after = db.query(func.count(Account.id)).scalar() or 0
    return {
        "synced": synced,
        "accounts_created": accounts_created_after - accounts_created_before,
    }


@router.get("/accounts")
def list_accounts(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns accounts with aggregate engagement stats computed on-the-fly."""
    q = db.query(Account)
    if search:
        escaped = search.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        q = q.filter(Account.domain.ilike(pattern) | Account.name.ilike(pattern))
    total = q.count()

    # Compute total_prospects dynamically per account (scoped to current user)
    _pc_q = db.query(Prospect.account_id, func.count(Prospect.id)).group_by(Prospect.account_id)
    if current_user:
        _pc_q = _pc_q.filter(Prospect.user_id == current_user.id)
    prospect_counts = dict(_pc_q.all())

    # Sort by dynamic prospect count (descending)
    accounts = q.all()
    accounts.sort(key=lambda a: prospect_counts.get(a.id, 0), reverse=True)
    accounts = accounts[offset:offset + limit]

    if not accounts:
        return {"total": total, "accounts": []}

    account_ids = [a.id for a in accounts]

    # Compute opened/replied counts by joining Account → Prospect → EmailSequence
    engagement = (
        db.query(
            Prospect.account_id,
            func.sum(case((EmailSequence.opened_at.isnot(None), 1), else_=0)).label("opened"),
            func.sum(case((EmailSequence.replied_at.isnot(None), 1), else_=0)).label("replied"),
        )
        .join(EmailSequence, EmailSequence.prospect_id == Prospect.id)
        .filter(Prospect.account_id.in_(account_ids))
        .filter(Prospect.user_id == current_user.id)
        .group_by(Prospect.account_id)
        .all()
    )
    eng_map = {row.account_id: {"opened": row.opened or 0, "replied": row.replied or 0} for row in engagement}

    # Compute meetings_booked dynamically from Reply table (single source of truth)
    meetings = (
        db.query(
            Prospect.account_id,
            func.count(Reply.id).label("meetings"),
        )
        .join(Reply, Reply.prospect_id == Prospect.id)
        .filter(
            Prospect.account_id.in_(account_ids),
            Reply.status == ReplyStatus.meeting_booked,
        )
        .group_by(Prospect.account_id)
        .all()
    )
    meetings_map = {row.account_id: row.meetings or 0 for row in meetings}

    return {
        "total": total,
        "accounts": [
            {
                "id": a.id,
                "domain": a.domain,
                "name": a.name,
                "industry": a.industry,
                "employee_count": a.employee_count,
                "total_prospects": prospect_counts.get(a.id, 0),
                "total_opened": eng_map.get(a.id, {}).get("opened", 0),
                "total_replied": eng_map.get(a.id, {}).get("replied", 0),
                "meetings_booked": meetings_map.get(a.id, 0),
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in accounts
        ],
    }


@router.post("/enrich")
def enrich_prospects_endpoint(
    req: EnrichRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Enrich prospects using Hunter.io, Proxycurl, and website scraping.
    Runs as a background job. Returns a job_id for polling.
    """
    import threading
    from ..db.database import SessionLocal
    from ..db.models import Job

    # Concurrency guard: reject if an enrich job is already running
    running_enrich = (
        db.query(Job)
        .filter(Job.type.contains("enrich"), Job.status == "running")
        .first()
    )
    if running_enrich:
        raise HTTPException(
            status_code=409,
            detail=f"An enrich job is already running (job_id={running_enrich.id}). Wait for it to finish.",
        )

    # FIX-6: scope counts and the eventual enrich worker to current user.
    user_filter = current_user.id if current_user else None
    if req.all:
        count_q = db.query(func.count(Prospect.id)).filter(Prospect.enriched_at.is_(None))
        if user_filter:
            count_q = count_q.filter(Prospect.user_id == user_filter)
        count = count_q.scalar() or 0
    elif req.prospect_ids:
        # Verify the supplied IDs belong to the caller before scheduling work.
        owner_q = db.query(Prospect.id).filter(Prospect.id.in_(req.prospect_ids))
        if user_filter:
            owner_q = owner_q.filter(Prospect.user_id == user_filter)
        owned = {r[0] for r in owner_q.all()}
        if len(owned) != len(req.prospect_ids):
            raise HTTPException(status_code=404, detail="One or more prospects not found.")
        count = len(req.prospect_ids)
    else:
        raise HTTPException(status_code=422, detail="Provide prospect_ids or set all=true.")

    if count == 0:
        return {"message": "No prospects to enrich.", "job_id": None}

    job = Job(
        id=str(uuid.uuid4()),
        user_id=current_user.id if current_user else None,
        type="enrich_prospects",
        status="pending",
        payload={"prospect_ids": req.prospect_ids, "all": req.all, "user_id": user_filter},
        progress=0,
        progress_detail="Queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    thread = threading.Thread(
        target=_run_enrich_job,
        args=(job.id, req.prospect_ids, req.all, user_filter),
        name=f"enrich-{job.id[:8]}",
        daemon=True,
    )
    thread.start()
    return {"job_id": job.id, "prospects_to_enrich": count}


def _run_enrich_job(job_id: str, prospect_ids: list[str] | None, enrich_all: bool, user_id: str | None = None):
    """Background worker for prospect enrichment.

    FIX-6: scoped to ``user_id`` so the worker never enriches another rep's
    prospects, even if the request payload omits explicit IDs.
    """
    from ..db.database import SessionLocal
    from ..db.models import Job
    from ..services.enrichment import EnrichmentClient
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        job = db.query(Job).filter_by(id=job_id).first()
        if not job:
            return
        job.status = "running"
        job.progress_detail = "Loading prospects..."
        db.commit()

        q = db.query(Prospect)
        if user_id:
            q = q.filter(Prospect.user_id == user_id)
        if enrich_all:
            q = q.filter(Prospect.enriched_at.is_(None))
        elif prospect_ids:
            q = q.filter(Prospect.id.in_(prospect_ids))
        prospects = q.all()
        total = len(prospects)

        if not prospects:
            job.status = "done"
            job.progress = 100
            job.progress_detail = "No prospects to enrich."
            job.result = {"enriched": 0, "errors": 0}
            db.commit()
            return

        client = EnrichmentClient()
        enriched_count = 0
        error_count = 0

        try:
            for i, p in enumerate(prospects):
                db.refresh(job)
                if job.status == "cancelled":
                    break
                try:
                    p_dict = {
                        "first_name": p.first_name,
                        "last_name": p.last_name or "",
                        "email": p.email,
                        "company": p.company or "",
                        "company_website": p.company_website or "",
                        "linkedin_url": p.linkedin_url or "",
                        "linkedin_headline": p.linkedin_headline or "",
                        "linkedin_summary": p.linkedin_summary or "",
                        "company_description": p.company_description or "",
                    }
                    result = client.enrich(p_dict)
                    # Apply enriched fields back to prospect
                    if result.get("linkedin_url") and not p.linkedin_url:
                        p.linkedin_url = result["linkedin_url"]
                    if result.get("linkedin_headline") and not p.linkedin_headline:
                        p.linkedin_headline = result["linkedin_headline"]
                    if result.get("linkedin_summary") and not p.linkedin_summary:
                        p.linkedin_summary = result["linkedin_summary"]
                    if result.get("company_description") and not p.company_description:
                        p.company_description = result["company_description"]
                    if result.get("email") and not p.email:
                        p.email = result["email"]
                    p.enriched_at = datetime.now(timezone.utc)
                    enriched_count += 1
                except Exception as e:
                    logger.error(f"Enrichment failed for {p.email}: {e}")
                    error_count += 1

                if (i + 1) % 10 == 0 or (i + 1) == total:
                    job.progress = int((i + 1) / total * 100)
                    job.progress_detail = f"Enriched {enriched_count}/{total} ({error_count} errors)..."
                    db.commit()
        finally:
            client.close()

        job.status = "done" if error_count == 0 else "completed_with_errors"
        job.progress = 100
        job.progress_detail = f"Done  -  {enriched_count} enriched, {error_count} errors."
        job.result = {"enriched": enriched_count, "errors": error_count, "total": total}
        db.commit()

    except Exception as e:
        logger.error(f"Enrich job {job_id} crashed: {e}", exc_info=True)
        try:
            job = db.query(Job).filter_by(id=job_id).first()
            if job:
                job.status = "failed"
                job.error = str(e)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ── Sales Navigator import via PhantomBuster ────────────────────────────────


class SalesNavImportRequest(BaseModel):
    search_url: str          # Sales Navigator search URL
    account_id: str          # LinkedIn Account ID (which account has Sales Nav)
    segment_id: Optional[str] = None  # Optional: assign to segment
    max_leads: int = 100     # Max leads to import


def _get_pb_client_for_account(account: LinkedInAccount):
    """Build a PhantomBusterClient using the account's API key, or fall back to global."""
    from ..services.phantombuster import PhantomBusterClient
    from ..services.crypto import decrypt

    api_key = None
    if account.pb_api_key_encrypted:
        try:
            api_key = decrypt(account.pb_api_key_encrypted)
        except Exception:
            logger.warning("Failed to decrypt PB API key for account %s, falling back to global", account.id)
    return PhantomBusterClient(api_key=api_key)


def _fetch_salesnav_result(pb, container_id: str) -> list[dict] | None:
    """Fetch and normalise PB container result into a list of profile dicts.

    Returns ``None`` if the container is still running, raises on error.
    Adapted from conference routes_leads._fetch_salesnav_result.
    """
    container = pb.get_container(container_id)
    status = container.get("status")

    if status in ("running", "starting", "launched"):
        return None  # still in progress

    if status in ("error", "crashed"):
        raise HTTPException(502, f"Sales Nav phantom failed with status: {status}")

    if status != "finished":
        return None  # unknown — treat as still running

    raw = pb.get_container_result(container_id)
    result_data: list | dict
    if isinstance(raw, dict) and "resultObject" in raw:
        ro = raw["resultObject"]
        if ro is None:
            result_data = []
        elif isinstance(ro, str):
            result_data = json.loads(ro)
        elif isinstance(ro, list):
            result_data = ro
        elif isinstance(ro, dict):
            result_data = ro
        else:
            result_data = []
    else:
        result_data = raw if raw else []

    if isinstance(result_data, list) and not result_data:
        return []

    # PB Sales Nav may return {csvURL, jsonUrl} pointers — fetch the JSON file
    profiles: list[dict] = []
    if isinstance(result_data, dict):
        json_url = result_data.get("jsonUrl") or result_data.get("jsonURL")
        if json_url:
            import requests as _requests
            try:
                resp = _requests.get(json_url, timeout=30)
                resp.raise_for_status()
                profiles = resp.json()
            except Exception as e:
                logger.error("Failed to fetch Sales Nav results from %s: %s", json_url, e)
        elif isinstance(result_data.get("resultObject"), str):
            profiles = json.loads(result_data["resultObject"])
    elif isinstance(result_data, list):
        profiles = result_data

    if not isinstance(profiles, list):
        profiles = []
    return profiles


def _import_salesnav_as_prospects(
    profiles: list[dict],
    db: Session,
    current_user: User,
    segment_id: str | None = None,
) -> dict:
    """Parse PB Sales Nav profiles and import as Prospect rows.

    Returns ``{"imported": int, "duplicates": int, "failed": int}``.
    """
    imported = 0
    duplicates = 0
    failed = 0

    for p in profiles:
        linkedin_url = (
            p.get("defaultProfileUrl")
            or p.get("linkedInProfileUrl")
            or p.get("profileUrl")
            or p.get("url")
            or ""
        )
        first = p.get("firstName") or p.get("first_name") or ""
        last = p.get("lastName") or p.get("last_name") or ""
        company = p.get("companyName") or p.get("company") or ""
        title = p.get("title") or p.get("jobTitle") or p.get("headline") or ""
        location = p.get("location") or ""

        if not linkedin_url:
            failed += 1
            continue

        # Dedup by linkedin_url within user's prospects
        dedup_q = db.query(Prospect).filter(
            Prospect.user_id == current_user.id,
            Prospect.linkedin_url == linkedin_url,
        )
        if dedup_q.first():
            duplicates += 1
            continue

        # Sales Nav doesn't provide emails — use a deterministic placeholder
        # so the NOT NULL + unique(user_id, email) constraints are satisfied.
        placeholder_email = f"salesnav-{hashlib.sha256(linkedin_url.encode()).hexdigest()[:12]}@placeholder.local"

        prospect = Prospect(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            first_name=first or "Unknown",
            last_name=last,
            email=placeholder_email,
            title=title,
            company=company,
            location=location,
            linkedin_url=linkedin_url,
            segment_id=segment_id,
            status=ProspectStatus.new,
        )

        nested = db.begin_nested()
        try:
            db.add(prospect)
            db.flush()
            imported += 1
        except _IntegrityError:
            nested.rollback()
            duplicates += 1
            logger.debug("SalesNav dedup skip (integrity): %s", linkedin_url)

    db.commit()
    return {"imported": imported, "duplicates": duplicates, "failed": failed}


@router.post("/import-salesnav")
def import_from_salesnav(
    req: SalesNavImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Launch PB Sales Navigator Search Export phantom for regular prospect import.

    Returns ``{"status": "launched", "job_id": "...", ...}`` so the frontend
    can poll ``GET /import-salesnav/status`` for results.
    """
    if not current_user:
        raise HTTPException(401, "Authentication required.")

    if not req.search_url or "linkedin.com" not in req.search_url:
        raise HTTPException(400, "Provide a valid LinkedIn Sales Navigator search URL.")

    # Load the LinkedInAccount and verify it has a Sales Nav phantom configured
    account = db.query(LinkedInAccount).filter_by(id=req.account_id, is_active=True).first()
    if not account:
        raise HTTPException(404, "LinkedIn account not found or inactive.")

    if not account.sales_nav_agent_id:
        raise HTTPException(
            400,
            f"LinkedIn account '{account.name}' does not have a Sales Nav phantom configured. "
            "Edit the account and set the Sales Nav Agent ID.",
        )

    # Validate segment_id if provided
    if req.segment_id:
        seg = db.query(AudienceSegment).filter_by(id=req.segment_id).first()
        if not seg:
            raise HTTPException(404, f"Segment '{req.segment_id}' not found.")

    pb = _get_pb_client_for_account(account)

    try:
        launch = pb.launch_agent(account.sales_nav_agent_id, bonus_argument={
            "salesNavigatorSearchUrl": req.search_url,
            "numberOfResultsPerSearch": req.max_leads,
            "numberOfLinesPerLaunch": req.max_leads,
        })
        container_id = launch.get("containerId")
        if not container_id:
            raise HTTPException(502, "PB launch succeeded but returned no containerId.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Sales Nav phantom launch failed: %s", e, exc_info=True)
        raise HTTPException(502, f"Failed to launch Sales Nav phantom: {e}")

    return {
        "status": "launched",
        "job_id": container_id,
        "account_id": req.account_id,
        "max_leads": req.max_leads,
        "segment_id": req.segment_id,
    }


@router.get("/import-salesnav/status")
def salesnav_import_status(
    job_id: str = Query(...),
    account_id: str = Query(...),
    segment_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Poll PB container status for Sales Nav import.

    Returns one of:
    - ``{"status": "running"}``
    - ``{"status": "complete", "imported": N, "duplicates": N, "failed": N}``
    - ``{"status": "failed", "detail": "..."}``
    """
    if not current_user:
        raise HTTPException(401, "Authentication required.")

    account = db.query(LinkedInAccount).filter_by(id=account_id, is_active=True).first()
    if not account:
        raise HTTPException(404, "LinkedIn account not found or inactive.")

    pb = _get_pb_client_for_account(account)

    try:
        profiles = _fetch_salesnav_result(pb, job_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Sales Nav status check failed for container %s: %s", job_id, e, exc_info=True)
        return {"status": "failed", "detail": str(e)}

    if profiles is None:
        return {"status": "running"}

    if not profiles:
        return {"status": "complete", "imported": 0, "duplicates": 0, "failed": 0}

    result = _import_salesnav_as_prospects(
        profiles,
        db=db,
        current_user=current_user,
        segment_id=segment_id,
    )
    return {"status": "complete", **result}


def _prospect_to_dict(p: Prospect) -> dict:
    return {
        "id": p.id,
        "user_id": p.user_id,
        "user_name": p.user.name if p.user_id and hasattr(p, "user") and p.user else None,
        "apollo_id": p.apollo_id,
        "first_name": p.first_name,
        "last_name": p.last_name,
        "email": p.email,
        "title": p.title,
        "company": p.company,
        "industry": p.industry,
        "headcount": p.headcount,
        "location": p.location,
        "linkedin_url": p.linkedin_url,
        "company_website": p.company_website,
        "account_id": p.account_id,
        "segment_id": p.segment_id,
        # Uses already-loaded relationship (joinedload)  -  no extra query.
        "segment_name": p.segment.name if p.segment else None,
        "status": p.status.value if p.status else "pending",
        "email_validation_status": p.email_validation_status,
        "email_validation_sub_status": p.email_validation_sub_status,
        "email_validated_at": p.email_validated_at.isoformat() if p.email_validated_at else None,
        "engagement_score": getattr(p, "engagement_score", 0) or 0,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
