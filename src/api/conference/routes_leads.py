"""
Lead CRUD: import, list, conferences, delete, bulk-delete, save-email,
enrich, enrich-all, costs, analytics.

P4-1: extracted from the old monolith.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ...config import MODEL_SMART, get_model_pricing
from ...db.database import get_db
from ...db.models import ConferenceLead, User
from ...services.enrichment import EnrichmentClient
from ..auth import get_current_user
from ._shared import (
    BulkDeleteRequest,
    SaveEmailRequest,
    _MAX_UPLOAD_BYTES,
    _check_ownership,
    _lead_to_dict,
)
from .parser import _parse_csv, _parse_excel
from .prompts import _DEFAULT_SEQ_STEPS
from .service import _best_effort_recall_outlook_draft, _load_seq_steps

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conference"])


@router.post("/import")
async def import_leads(
    request: Request,
    conference_name: str,
    outreach_type: str = "post",
    event_date: Optional[str] = None,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload an Excel or CSV file and import leads for a conference."""
    # FIX-35: require an authenticated user whenever the system has users.
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")

    # FIX-35: reject oversize uploads BEFORE buffering them in memory.
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"File too large. Maximum is {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        except ValueError:
            pass

    if not file.filename:
        raise HTTPException(400, "Filename is required.")
    name = file.filename.lower()
    if name.endswith((".xlsx", ".xls", ".xlsm")):
        parser = _parse_excel
    elif name.endswith(".csv"):
        parser = _parse_csv
    else:
        raise HTTPException(400, "File must be an Excel (.xlsx / .xls) or CSV (.csv) file.")

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large ({len(content)} bytes). Maximum is 10 MB.")
    try:
        rows = parser(content)
    except ValueError as e:
        logger.warning("File parse error during conference import: %s", e)
        raise HTTPException(400, "Could not parse file. Ensure it has an 'email' column and is a valid CSV/Excel file.")

    # Parse event_date if provided
    _event_date = None
    if event_date:
        try:
            from datetime import date as _date_cls
            _event_date = _date_cls.fromisoformat(event_date)
        except (ValueError, TypeError):
            raise HTTPException(400, "event_date must be YYYY-MM-DD format.")

    if outreach_type not in ("pre", "post"):
        raise HTTPException(400, "outreach_type must be 'pre' or 'post'.")

    imported = 0
    skipped = 0
    skipped_reasons: dict[str, int] = {"email": 0, "linkedin_url": 0}
    for row in rows:
        # Cross-source dedup: check both email AND linkedin_url so the
        # same person imported from multiple sources isn't duplicated.

        dedup_conditions = [ConferenceLead.email == row["email"]]
        li_url = row.get("linkedin_url")
        if li_url:
            dedup_conditions.append(ConferenceLead.linkedin_url == li_url)
        dedupe_q = db.query(ConferenceLead).filter(
            ConferenceLead.conference_name == conference_name,
            ConferenceLead.outreach_type == outreach_type,
            or_(*dedup_conditions),
        )
        if current_user:
            dedupe_q = dedupe_q.filter(ConferenceLead.user_id == current_user.id)
        match = dedupe_q.first()
        if match:
            if match.email == row["email"]:
                skipped_reasons["email"] += 1
                logger.debug("Dedup skip (email match): %s", row["email"])
            else:
                skipped_reasons["linkedin_url"] += 1
                logger.debug("Dedup skip (linkedin_url match): %s", li_url)
            skipped += 1
            continue

        lead = ConferenceLead(
            id=str(uuid.uuid4()),
            conference_name=conference_name,
            outreach_type=outreach_type,
            event_date=_event_date,
            email=row.get("email"),
            first_name=row.get("first_name"),
            last_name=row.get("last_name"),
            company=row.get("company"),
            title=row.get("title"),
            notes=row.get("notes"),
            linkedin_url=row.get("linkedin_url"),
            company_website=row.get("company_website"),
            cc=row.get("cc"),
            category=row.get("category"),
            raw_data=row.get("raw_data"),
            user_id=current_user.id if current_user else None,
        )
        db.add(lead)
        imported += 1

    db.commit()
    return {"imported": imported, "skipped": skipped, "skipped_reasons": skipped_reasons, "total": len(rows)}


@router.get("/leads")
def list_leads(
    conference_name: Optional[str] = None,
    outreach_type: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(ConferenceLead)
    if current_user:
        q = q.filter(ConferenceLead.user_id == current_user.id)
    if outreach_type:
        q = q.filter(ConferenceLead.outreach_type == outreach_type)
    if conference_name:
        q = q.filter_by(conference_name=conference_name)
    if search:
        escaped = search.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        q = q.filter(
            ConferenceLead.email.ilike(pattern)
            | ConferenceLead.first_name.ilike(pattern)
            | ConferenceLead.last_name.ilike(pattern)
            | ConferenceLead.company.ilike(pattern)
        )
    q = q.order_by(ConferenceLead.created_at.desc())
    total = q.count()
    leads = q.offset(offset).limit(min(limit, 1000)).all()

    # ── Orch state + LinkedIn status for pre-conference leads ────────────
    lead_ids = [l.id for l in leads]
    orch_map: dict[str, object] = {}
    li_connect_sent_set: set[str] = set()
    li_message_sent_set: set[str] = set()
    step_type_map: dict[str, str] = {}  # step_id → step_type string

    if lead_ids and outreach_type == "pre":
        from ...orchestration.orm import OrchSequenceStateORM, EngagementEventORM
        from ...orchestration.config import get_sequence_config

        orch_states = (
            db.query(OrchSequenceStateORM)
            .filter(OrchSequenceStateORM.lead_id.in_(lead_ids))
            .all()
        )
        for s in orch_states:
            orch_map[s.lead_id] = s

        # Build step_id → step_type map for completed_steps resolution
        seq_ids = {s.sequence_id for s in orch_states}
        for sid in seq_ids:
            try:
                cfg = get_sequence_config(sid)
                for st in cfg.steps:
                    step_type_map[st.step_id] = (
                        st.step_type.value if hasattr(st.step_type, "value") else str(st.step_type)
                    )
            except Exception:
                pass

        # LinkedIn ground truth from engagement events
        li_events = (
            db.query(EngagementEventORM.lead_id, EngagementEventORM.event_type)
            .filter(
                EngagementEventORM.lead_id.in_(lead_ids),
                EngagementEventORM.event_type.in_([
                    "linkedin_connect_sent",
                    "linkedin_message_sent",
                ]),
            )
            .distinct()
            .all()
        )
        for row in li_events:
            if row.event_type == "linkedin_connect_sent":
                li_connect_sent_set.add(row.lead_id)
            elif row.event_type == "linkedin_message_sent":
                li_message_sent_set.add(row.lead_id)

    items = []
    for l in leads:
        d = _lead_to_dict(l)
        os = orch_map.get(l.id)
        if os:
            completed_types = {
                step_type_map.get(sid, "")
                for sid in (os.completed_steps or [])
            }
            d["orch_state"] = {
                "status": os.status,
                "current_step_index": os.current_step_index,
                "enrolled_at": os.enrolled_at.isoformat() if os.enrolled_at else None,
                "scheduled_next_at": os.scheduled_next_at.isoformat() if os.scheduled_next_at else None,
                "engagement_score": os.engagement_score or 0,
                "completed_steps": os.completed_steps if isinstance(os.completed_steps, list) else [],
                "sequence_id": os.sequence_id,
            }
            d["linkedin_connect_sent"] = l.id in li_connect_sent_set
            d["linkedin_connect_queued"] = "send_linkedin_connect" in completed_types
            d["linkedin_message_sent"] = l.id in li_message_sent_set
            d["linkedin_message_queued"] = "send_linkedin_message" in completed_types
        items.append(d)

    return {"items": items, "total": total}


@router.get("/conferences")
def list_conferences(
    outreach_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return distinct conference names, optionally filtered by outreach_type."""
    q = db.query(ConferenceLead.conference_name)
    if current_user:
        q = q.filter(ConferenceLead.user_id == current_user.id)
    if outreach_type:
        q = q.filter(ConferenceLead.outreach_type == outreach_type)
    rows = q.distinct().all()
    return [r[0] for r in rows]


@router.delete("/leads/{lead_id}")
def delete_lead(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user, db)
    _best_effort_recall_outlook_draft(lead, current_user)
    db.delete(lead)
    db.commit()
    return {"ok": True}


@router.post("/leads/bulk-delete")
def bulk_delete_leads(
    req: BulkDeleteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    FIX-25: Delete many conference leads in a single transaction, scoped to
    the current user. Frontend uses this to clear lists without N sequential
    DELETE requests that freeze the UI.

    Returns ``{"deleted": <int>}``. Best-effort recalls Outlook drafts before
    deleting each lead.
    """
    if not req.ids:
        return {"deleted": 0}
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")

    q = db.query(ConferenceLead).filter(ConferenceLead.id.in_(req.ids))
    if current_user:
        q = q.filter(ConferenceLead.user_id == current_user.id)
    leads = q.all()

    for lead in leads:
        _best_effort_recall_outlook_draft(lead, current_user)
        db.delete(lead)
    db.commit()
    return {"deleted": len(leads)}


@router.delete("/conferences/{conference_name}")
def delete_conference(
    conference_name: str,
    outreach_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete an entire conference and all its leads, optionally filtered by outreach_type."""
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")

    q = db.query(ConferenceLead).filter(ConferenceLead.conference_name == conference_name)
    if current_user:
        q = q.filter(ConferenceLead.user_id == current_user.id)
    if outreach_type:
        q = q.filter(ConferenceLead.outreach_type == outreach_type)
    leads = q.all()
    if not leads:
        raise HTTPException(404, "Conference not found or no leads belong to you.")

    for lead in leads:
        _best_effort_recall_outlook_draft(lead, current_user)
        db.delete(lead)
    db.commit()
    logger.info("Deleted conference %r (%d leads) for user %s", conference_name, len(leads),
                current_user.id if current_user else "anon")
    return {"ok": True, "deleted": len(leads)}


@router.post("/enrich/{lead_id}")
def enrich_lead(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Enrich a single conference lead using Hunter + website scraper."""
    lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user, db)

    enricher = EnrichmentClient()
    try:
        result = enricher.enrich({
            "first_name": lead.first_name or "",
            "last_name": lead.last_name or "",
            "email": lead.email,
            "company": lead.company or "",
            "linkedin_url": lead.linkedin_url,
            "company_website": lead.company_website,
        })

        if result.get("linkedin_url"):
            lead.linkedin_url = result["linkedin_url"]
        if result.get("linkedin_headline"):
            lead.linkedin_headline = result["linkedin_headline"]
        if result.get("company_description"):
            lead.company_description = result["company_description"]
        if result.get("company_website"):
            lead.company_website = result["company_website"]
        lead.enriched_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as e:
        logger.error("Enrichment failed for %s: %s", lead.email, e, exc_info=True)
        raise HTTPException(500, "Enrichment failed. Please try again.")
    finally:
        enricher.close()

    return _lead_to_dict(lead)


@router.post("/enrich-all")
def enrich_all(conference_name: Optional[str] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Enrich all unenriched leads (optionally filtered by conference)."""
    # FIX-21: never operate on the global lead set when no user context exists.
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")
    q = db.query(ConferenceLead).filter(ConferenceLead.enriched_at.is_(None))
    if current_user:
        q = q.filter(ConferenceLead.user_id == current_user.id)
    if conference_name:
        q = q.filter_by(conference_name=conference_name)
    leads = q.all()

    enricher = EnrichmentClient()
    done, failed = 0, 0
    try:
        for lead in leads:
            # FIX-34: per-lead savepoint so a single failure doesn't poison
            # the outer transaction or expunge other in-flight changes.
            sp = db.begin_nested()
            try:
                result = enricher.enrich({
                    "first_name": lead.first_name or "",
                    "last_name": lead.last_name or "",
                    "email": lead.email,
                    "company": lead.company or "",
                    "linkedin_url": lead.linkedin_url,
                    "company_website": lead.company_website,
                })
                if result.get("linkedin_url"):
                    lead.linkedin_url = result["linkedin_url"]
                if result.get("linkedin_headline"):
                    lead.linkedin_headline = result["linkedin_headline"]
                if result.get("company_description"):
                    lead.company_description = result["company_description"]
                if result.get("company_website"):
                    lead.company_website = result["company_website"]
                lead.enriched_at = datetime.now(timezone.utc)
                sp.commit()
                done += 1
            except Exception as e:
                logger.warning(f"Enrichment failed for {lead.email}: {e}")
                try:
                    sp.rollback()
                except Exception:
                    pass
                failed += 1
    finally:
        enricher.close()

    if done:
        db.commit()
    return {"enriched": done, "failed": failed}


@router.get("/costs")
def conference_costs(
    user_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    FIX-4: Token cost breakdown per event. Non-admin callers see only their
    own spend; admins may pass `?user_id=` to view another user.
    """
    pricing = get_model_pricing(MODEL_SMART)
    inp_rate = pricing["input"]   # USD per million tokens
    out_rate = pricing["output"]

    base = db.query(
        ConferenceLead.conference_name,
        ConferenceLead.user_id,
        func.sum(ConferenceLead.tokens_input).label("inp"),
        func.sum(ConferenceLead.tokens_output).label("out"),
        func.count(ConferenceLead.id).label("leads"),
    )

    # Scope by user. Admin can request another user's spend; reps see only own.
    is_admin = bool(current_user and current_user.role and current_user.role.value == "admin")
    if current_user and not is_admin:
        base = base.filter(ConferenceLead.user_id == current_user.id)
    elif is_admin and user_id:
        base = base.filter(ConferenceLead.user_id == user_id)

    rows = base.group_by(ConferenceLead.conference_name, ConferenceLead.user_id).all()

    user_map = {u.id: u.name for u in db.query(User).all()}
    events: dict[str, dict] = {}
    for row in rows:
        name = row.conference_name or "Unknown"
        inp = row.inp or 0
        out = row.out or 0
        cost = (inp / 1_000_000 * inp_rate) + (out / 1_000_000 * out_rate)
        if name not in events:
            events[name] = {"event": name, "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0, "leads": 0, "users": []}
        events[name]["tokens_input"] += inp
        events[name]["tokens_output"] += out
        events[name]["cost_usd"] += cost
        events[name]["leads"] += row.leads
        if row.user_id:
            events[name]["users"].append({
                "user_id": row.user_id,
                "user_name": user_map.get(row.user_id, "Unknown"),
                "tokens_input": inp,
                "tokens_output": out,
                "cost_usd": round(cost, 4),
                "leads": row.leads,
            })

    # Round totals
    for ev in events.values():
        ev["cost_usd"] = round(ev["cost_usd"], 4)

    total_inp = sum(e["tokens_input"] for e in events.values())
    total_out = sum(e["tokens_output"] for e in events.values())
    total_cost = (total_inp / 1_000_000 * inp_rate) + (total_out / 1_000_000 * out_rate)

    # Dynamic sequence length so the UI can rewrite its "6-email" copy to
    # match whatever `conference_sequence_config` the operator has set.
    try:
        sequence_step_count = len(_load_seq_steps(db, user_id=current_user.id if current_user else None))
    except Exception:
        sequence_step_count = len(_DEFAULT_SEQ_STEPS)

    return {
        "model": MODEL_SMART,
        "pricing": {"input_per_mtok": inp_rate, "output_per_mtok": out_rate},
        "total_tokens_input": total_inp,
        "total_tokens_output": total_out,
        "total_cost_usd": round(total_cost, 4),
        "sequence_step_count": sequence_step_count,
        "events": list(events.values()),
    }


@router.patch("/leads/{lead_id}/email")
def save_email(lead_id: str, req: SaveEmailRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Save manually edited subject/body for a lead."""
    lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user, db)
    lead.generated_subject = req.subject
    lead.generated_body = req.body
    lead.generated_at = datetime.now(timezone.utc)
    db.commit()
    return _lead_to_dict(lead)


@router.patch("/leads/{lead_id}/notes")
def update_lead_notes(lead_id: str, body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Add, update, or clear the user note on a conference lead."""
    lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user, db)
    lead.user_note = (body.get("notes") or "").strip() or None
    db.commit()
    return _lead_to_dict(lead)


@router.get("/analytics")
def conference_analytics(
    conference_name: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Funnel analytics for conference leads: drafts, replies, meetings."""
    q = db.query(ConferenceLead)
    if current_user:
        q = q.filter(ConferenceLead.user_id == current_user.id)
    if conference_name:
        q = q.filter(ConferenceLead.conference_name == conference_name)

    leads = q.all()
    total_leads = len(leads)
    total_replied = 0
    meetings_booked = 0

    # Dynamically count drafted emails per step (not hardcoded to 3)
    email_step_counts: dict[int, int] = {}  # step_number -> drafted count
    max_email_step = 0

    for lead in leads:
        if lead.replied_at is not None:
            total_replied += 1
        if lead.meeting_booked:
            meetings_booked += 1

        # Email 1 is drafted when draft_created_at is set
        if lead.draft_created_at is not None:
            email_step_counts[1] = email_step_counts.get(1, 0) + 1
            if 1 > max_email_step:
                max_email_step = 1

        # Check generated_sequence for subsequent drafted steps
        seq_raw = lead.generated_sequence
        if seq_raw:
            try:
                seq = json.loads(seq_raw) if isinstance(seq_raw, str) else seq_raw
            except (json.JSONDecodeError, TypeError):
                seq = None
            if seq and isinstance(seq, dict):
                drafted_steps = seq.get("_drafted_steps", [])
                # Count email keys in the sequence to determine max steps
                email_keys = [k for k in seq.keys() if k.startswith("email") and not k.startswith("email1_")]
                if email_keys:
                    nums = []
                    for k in email_keys:
                        # Extract number from "email2", "email3", etc.
                        num_str = k.replace("email", "").split("_")[0]
                        if num_str.isdigit():
                            nums.append(int(num_str))
                    if nums:
                        step_max = max(nums)
                        if step_max > max_email_step:
                            max_email_step = step_max

                for step_key in drafted_steps:
                    if step_key.startswith("email"):
                        num_str = step_key.replace("email", "")
                        if num_str.isdigit():
                            n = int(num_str)
                            email_step_counts[n] = email_step_counts.get(n, 0) + 1

    email1_count = email_step_counts.get(1, 0)
    reply_rate = round(total_replied / email1_count * 100, 1) if email1_count else 0.0

    # Build per-step drafted array (dynamic length)
    steps_drafted = []
    for i in range(1, max_email_step + 1):
        steps_drafted.append({
            "step": i,
            "label": f"Email {i}",
            "drafted": email_step_counts.get(i, 0),
        })

    # Build replied_leads list with stage attribution
    replied_leads = []
    for lead in leads:
        if lead.replied_at is None:
            continue
        # Determine which email step they replied to (highest drafted step)
        drafted_steps = []
        seq_raw = lead.generated_sequence
        if seq_raw:
            try:
                seq = json.loads(seq_raw) if isinstance(seq_raw, str) else seq_raw
            except (json.JSONDecodeError, TypeError):
                seq = None
            if seq and isinstance(seq, dict):
                drafted_steps = seq.get("_drafted_steps", [])

        # Find the highest drafted email step
        reply_stage = 1
        for step_key in drafted_steps:
            if step_key.startswith("email"):
                num_str = step_key.replace("email", "")
                if num_str.isdigit():
                    n = int(num_str)
                    if n > reply_stage:
                        reply_stage = n

        replied_leads.append({
            "id": lead.id,
            "name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
            "email": lead.email,
            "company": lead.company or "",
            "reply_stage": reply_stage,
            "replied_at": lead.replied_at.isoformat() if lead.replied_at else None,
            "reply_subject": lead.reply_subject,
            "reply_snippet": (lead.reply_snippet or "")[:200],
        })

    return {
        "total_leads": total_leads,
        "steps_drafted": steps_drafted,
        "total_replied": total_replied,
        "meetings_booked": meetings_booked,
        "reply_rate": reply_rate,
        "replied_leads": replied_leads,
    }


@router.patch("/leads/{lead_id}/meeting-booked")
def toggle_meeting_booked(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Toggle the meeting_booked flag for a conference lead."""
    lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user, db)
    lead.meeting_booked = not lead.meeting_booked
    db.commit()
    return {"id": lead.id, "meeting_booked": lead.meeting_booked}


# ── Apollo prospecting for Pre-Conference ────────────────────────────────────


class _ApolloSearchRequest(BaseModel):
    conference_name: str
    event_date: Optional[str] = None
    titles: Optional[list[str]] = None
    seniorities: Optional[list[str]] = None
    industries: Optional[list[str]] = None
    employee_ranges: Optional[list[str]] = None
    locations: Optional[list[str]] = None
    limit: int = 50


class _ApolloConfirmRequest(BaseModel):
    conference_name: str
    event_date: Optional[str] = None
    apollo_ids: list[str]


@router.post("/import-apollo")
def apollo_search_preview(
    req: _ApolloSearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Search Apollo for leads matching filters. Returns a preview (names,
    titles, companies) without spending reveal credits. The caller picks
    which contacts to import, then calls /import-apollo/confirm."""
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")

    has_filter = any([req.titles, req.seniorities, req.industries, req.employee_ranges, req.locations])
    if not has_filter:
        raise HTTPException(422, "At least one search filter is required.")

    from ...services.apollo import ApolloClient
    apollo = ApolloClient()
    try:
        result = apollo.search_people(
            titles=req.titles,
            seniorities=req.seniorities,
            industries=req.industries,
            employee_ranges=req.employee_ranges,
            locations=req.locations,
            per_page=min(req.limit, 100),
        )
    except Exception as e:
        logger.error("Apollo search failed: %s", e, exc_info=True)
        raise HTTPException(502, f"Apollo search failed: {e}")

    people = result.get("people", [])
    # Return preview data (no emails, no credits spent)
    preview = []
    for p in people:
        apollo_id = p.get("id")
        if not apollo_id:
            continue
        preview.append({
            "apollo_id": apollo_id,
            "first_name": p.get("first_name", ""),
            "last_name": p.get("last_name", ""),
            "title": p.get("title", ""),
            "company": (p.get("organization") or {}).get("name", ""),
            "linkedin_url": p.get("linkedin_url", ""),
            "city": p.get("city", ""),
            "country": p.get("country", ""),
            "has_email": bool(p.get("has_email")),
        })

    return {"results": preview, "total": len(preview)}


@router.post("/import-apollo/confirm")
def apollo_confirm_import(
    req: _ApolloConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reveal selected Apollo contacts (1 credit each) and import as
    pre-conference leads."""
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")

    if not req.apollo_ids:
        return {"imported": 0, "skipped": 0, "failed": 0}

    # Parse event_date
    _event_date = None
    if req.event_date:
        try:
            from datetime import date as _date_cls
            _event_date = _date_cls.fromisoformat(req.event_date)
        except (ValueError, TypeError):
            raise HTTPException(400, "event_date must be YYYY-MM-DD format.")

    from ...services.apollo import ApolloClient, reveal_with_cache, _extract_email
    from ...db.database import SessionLocal as _SessionLocal
    apollo = ApolloClient()
    try:
        revealed = reveal_with_cache(apollo, req.apollo_ids, _SessionLocal)
    except Exception as e:
        logger.error("Apollo reveal failed: %s", e, exc_info=True)
        raise HTTPException(502, f"Apollo reveal failed: {e}")

    imported = 0
    skipped = 0
    skipped_reasons: dict[str, int] = {"email": 0, "linkedin_url": 0}
    failed = 0
    for person in revealed:
        email = _extract_email(person)
        if not email:
            failed += 1
            continue

        # Cross-source dedup: check both email AND linkedin_url so the
        # same person imported from Apollo and Sales Nav isn't duplicated.
        li_url = person.get("linkedin_url") or ""
        dedupe_q = db.query(ConferenceLead).filter(
            ConferenceLead.conference_name == req.conference_name,
            ConferenceLead.outreach_type == "pre",
        )
        if current_user:
            dedupe_q = dedupe_q.filter(ConferenceLead.user_id == current_user.id)

        dedupe_q = dedupe_q.filter(or_(
            ConferenceLead.email == email,
            (ConferenceLead.linkedin_url == li_url) if li_url else False,
        ))
        match = dedupe_q.first()
        if match:
            if match.email == email:
                skipped_reasons["email"] += 1
                logger.debug("Apollo dedup skip (email match): %s", email)
            else:
                skipped_reasons["linkedin_url"] += 1
                logger.debug("Apollo dedup skip (linkedin_url match): %s", li_url)
            skipped += 1
            continue

        org = person.get("organization") or {}
        lead = ConferenceLead(
            id=str(uuid.uuid4()),
            conference_name=req.conference_name,
            outreach_type="pre",
            event_date=_event_date,
            email=email,
            first_name=person.get("first_name"),
            last_name=person.get("last_name"),
            company=org.get("name"),
            title=person.get("title"),
            linkedin_url=person.get("linkedin_url"),
            company_website=org.get("website_url"),
            user_id=current_user.id if current_user else None,
            raw_data=json.dumps({
                "apollo_id": person.get("id"),
                "city": person.get("city"),
                "country": person.get("country"),
                "seniority": person.get("seniority"),
                "departments": person.get("departments"),
                "industry": org.get("industry"),
                "employee_count": org.get("estimated_num_employees"),
            }),
        )
        db.add(lead)
        imported += 1

    db.commit()
    return {"imported": imported, "skipped": skipped, "skipped_reasons": skipped_reasons, "failed": failed}


# ── Sales Navigator search via PhantomBuster ─────────────────────────────────


class _SalesNavSearchRequest(BaseModel):
    conference_name: str
    event_date: Optional[str] = None
    search_url: str
    max_results: int = 100


def _import_salesnav_profiles(
    profiles: list[dict],
    conference_name: str,
    event_date,
    db: Session,
    current_user: User | None,
) -> dict:
    """Parse PB Sales Nav profiles and import as pre-conference leads.

    Returns ``{"imported": int, "skipped": int, "failed": int}``.
    Shared by the legacy inline path and the async status endpoint.
    """
    import hashlib

    imported = 0
    skipped = 0
    skipped_reasons: dict[str, int] = {"email": 0, "linkedin_url": 0}
    failed = 0

    for p in profiles:
        # Prefer defaultProfileUrl (clean /in/name) over linkedInProfileUrl (VMID format)
        linkedin_url = p.get("defaultProfileUrl") or p.get("linkedInProfileUrl") or p.get("profileUrl") or p.get("url") or ""
        first = p.get("firstName") or p.get("first_name") or ""
        last = p.get("lastName") or p.get("last_name") or ""
        email = p.get("email") or ""
        company = p.get("companyName") or p.get("company") or ""
        title = p.get("title") or p.get("jobTitle") or ""

        if not linkedin_url and not email:
            failed += 1
            continue

        # Cross-source dedup: check both email AND linkedin_url so the
        # same person imported from Apollo and Sales Nav isn't duplicated.

        dedup_conditions = []
        if email:
            dedup_conditions.append(ConferenceLead.email == email)
        if linkedin_url:
            dedup_conditions.append(ConferenceLead.linkedin_url == linkedin_url)
        dedupe_q = db.query(ConferenceLead).filter(
            ConferenceLead.conference_name == conference_name,
            ConferenceLead.outreach_type == "pre",
        )
        if current_user:
            dedupe_q = dedupe_q.filter(ConferenceLead.user_id == current_user.id)
        if dedup_conditions:
            dedupe_q = dedupe_q.filter(or_(*dedup_conditions))
        match = dedupe_q.first()
        if match:
            if email and match.email == email:
                skipped_reasons["email"] += 1
                logger.debug("SalesNav dedup skip (email match): %s", email)
            else:
                skipped_reasons["linkedin_url"] += 1
                logger.debug("SalesNav dedup skip (linkedin_url match): %s", linkedin_url)
            skipped += 1
            continue

        # FIX-4: never store a LinkedIn URL in the email column. When the
        # lead has no email, use a deterministic placeholder so the unique
        # index on (conference_name, email, outreach_type) doesn't conflict
        # across multiple email-less leads.
        effective_email = email or f"no-email-{hashlib.sha256(linkedin_url.encode()).hexdigest()[:12]}@placeholder.local"

        lead = ConferenceLead(
            id=str(uuid.uuid4()),
            conference_name=conference_name,
            outreach_type="pre",
            event_date=event_date,
            email=effective_email,
            first_name=first,
            last_name=last,
            company=company,
            title=title,
            linkedin_url=linkedin_url,
            user_id=current_user.id if current_user else None,
            raw_data=json.dumps({
                "source": "sales_navigator",
                "location": p.get("location") or "",
                "industry": p.get("industry") or "",
                "company_size": p.get("companySize") or "",
                "connection_degree": p.get("connectionDegree") or p.get("degree") or "",
                "duration_in_role": p.get("durationInRole") or "",
                "summary": (p.get("summary") or "")[:500],
            }),
        )
        db.add(lead)
        imported += 1

    db.commit()
    return {"imported": imported, "skipped": skipped, "skipped_reasons": skipped_reasons, "failed": failed}


def _fetch_salesnav_result(pb, container_id: str) -> list[dict] | None:
    """Fetch and normalise PB container result into a list of profile dicts.

    Returns ``None`` if the container is still running, raises on error.
    """
    container = pb.get_container(container_id)
    status = container.get("status")

    if status in ("running", "starting", "launched"):
        return None  # still in progress

    if status in ("error", "crashed"):
        raise HTTPException(502, f"Sales Nav phantom failed with status: {status}")

    if status != "finished":
        # Unknown status — treat as still running to avoid false positives
        return None

    raw = pb.get_container_result(container_id)
    # PB wraps in {resultObject: ...} — unwrap if needed
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


@router.post("/import-salesnav")
def salesnav_search(
    req: _SalesNavSearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Launch PB Sales Navigator Search Export phantom and return immediately.

    Returns ``{"status": "launched", "container_id": "...", ...}`` so the
    frontend can poll ``GET /import-salesnav/status`` for results instead of
    blocking a uvicorn worker thread.
    """
    import os

    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")

    if not req.search_url or "linkedin.com" not in req.search_url:
        raise HTTPException(400, "Provide a valid LinkedIn Sales Navigator search URL.")

    agent_id = os.getenv("PHANTOMBUSTER_SALESNAV_AGENT_ID")
    if not agent_id:
        raise HTTPException(
            501,
            "Sales Navigator phantom not configured. Set PHANTOMBUSTER_SALESNAV_AGENT_ID in .env "
            "after creating a 'LinkedIn Sales Navigator Search Export' phantom in PhantomBuster.",
        )

    # Validate event_date early so the caller gets a 400 before we spend PB credits
    if req.event_date:
        try:
            from datetime import date as _date_cls
            _date_cls.fromisoformat(req.event_date)
        except (ValueError, TypeError):
            raise HTTPException(400, "event_date must be YYYY-MM-DD format.")

    from ...services.phantombuster import PhantomBusterClient
    pb = PhantomBusterClient()

    # Launch the phantom with the search URL
    try:
        launch = pb.launch_agent(agent_id, bonus_argument={
            "salesNavigatorSearchUrl": req.search_url,
            "numberOfResultsPerSearch": req.max_results,
            "numberOfLinesPerLaunch": req.max_results,
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
        "container_id": container_id,
        "conference_name": req.conference_name,
        "max_results": req.max_results,
    }


@router.get("/import-salesnav/status")
def salesnav_status(
    container_id: str,
    conference_name: str,
    max_results: int = 100,
    event_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Poll PB container status.  Returns one of:

    - ``{"status": "running"}`` — phantom still executing
    - ``{"status": "done", "imported": N, "skipped": N, "failed": N}``
    - ``{"status": "error", "detail": "..."}``
    """
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")

    _event_date = None
    if event_date:
        try:
            from datetime import date as _date_cls
            _event_date = _date_cls.fromisoformat(event_date)
        except (ValueError, TypeError):
            raise HTTPException(400, "event_date must be YYYY-MM-DD format.")

    from ...services.phantombuster import PhantomBusterClient
    pb = PhantomBusterClient()

    try:
        profiles = _fetch_salesnav_result(pb, container_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Sales Nav status check failed for container %s: %s", container_id, e, exc_info=True)
        return {"status": "error", "detail": str(e)}

    if profiles is None:
        return {"status": "running"}

    if not profiles:
        return {"status": "done", "imported": 0, "skipped": 0, "failed": 0}

    result = _import_salesnav_profiles(
        profiles,
        conference_name=conference_name,
        event_date=_event_date,
        db=db,
        current_user=current_user,
    )
    return {"status": "done", **result}


# ── LinkedIn orchestration launch for pre-conference leads ────────────────


class _LaunchLinkedInRequest(BaseModel):
    conference_name: str


@router.post("/launch-linkedin")
def launch_linkedin_outreach(
    req: _LaunchLinkedInRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Enroll pre-conference leads into the LinkedIn orchestration engine."""
    import hashlib
    import traceback
    from datetime import timedelta

    try:
        from ...orchestration.config import register_sequence_config
        from ...orchestration.enums import StepType, OrchestrationStatus
        from ...orchestration.models import SequenceConfig, SequenceStep, StepCondition
        from ...orchestration.orm import OrchSequenceStateORM
        from ...db.models import SequenceConfigORM, AppSetting
    except Exception as imp_err:
        logger.error("launch-linkedin import error: %s", imp_err, exc_info=True)
        raise HTTPException(500, f"Import error: {imp_err}")

    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")

    # 1. Load the pre-conference sequence config
    from ..settings import _get_user_setting
    _conf_user_id = current_user.id if current_user else None
    raw_config = _get_user_setting(db, "pre_conference_sequence_config", _conf_user_id)
    if not raw_config:
        raise HTTPException(400, "No pre-conference sequence config found. Configure it in Settings.")

    try:
        config_data = json.loads(raw_config)
        all_steps = config_data.get("steps", [])
    except (json.JSONDecodeError, KeyError, TypeError):
        raise HTTPException(400, "Invalid pre-conference sequence config.")

    # 2. Resolve step type from type field OR preset name, then filter to LinkedIn
    for s in all_steps:
        if not s.get("type"):
            preset = (s.get("preset") or "").lower()
            if "linkedin_connect" in preset:
                s["type"] = "linkedin_connect"
            elif "linkedin_message" in preset:
                s["type"] = "linkedin_message"
            else:
                s["type"] = "email"
    li_steps = [s for s in all_steps if "linkedin" in (s.get("type", "") or "").lower()]
    if not li_steps:
        raise HTTPException(400, "No LinkedIn steps in sequence config. Add LinkedIn steps in the Sequence designer (Step 3).")

    # 3. Build a SequenceConfig
    sequence_id = f"preconf-li-{hashlib.sha256(req.conference_name.encode()).hexdigest()[:12]}"

    orch_steps = []
    for i, s in enumerate(li_steps):
        step_type_str = s.get("type", "")
        day_offset = int(s.get("day", i * 3))

        # Map step type
        if "connect" in step_type_str.lower():
            stype = StepType.send_linkedin_connect
            conditions = [
                StepCondition(condition_type="linkedin_url_exists", value=None),
                StepCondition(condition_type="no_reply", value=None),
                StepCondition(condition_type="no_unsubscribe", value=None),
            ]
        else:
            stype = StepType.send_linkedin_message
            conditions = [
                StepCondition(condition_type="linkedin_url_exists", value=None),
                StepCondition(condition_type="no_reply", value=None),
                StepCondition(condition_type="no_unsubscribe", value=None),
                StepCondition(condition_type="linkedin_connected", value=None),
            ]

        step_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"preconf-{stype.value}-day{day_offset}-{req.conference_name}"))
        orch_steps.append(SequenceStep(
            step_id=step_id,
            step_type=stype,
            day_offset=day_offset,
            template_id=None,
            conditions=conditions,
        ))

    seq_config = SequenceConfig(
        sequence_id=sequence_id,
        name=f"Pre-conference LinkedIn: {req.conference_name}",
        steps=orch_steps,
        timezone="Asia/Riyadh",
        business_days_only=False,
    )

    # 5. Persist to SequenceConfigORM (upsert by sequence_id)
    existing_orm = db.query(SequenceConfigORM).filter_by(id=sequence_id).first()
    config_json = seq_config.model_dump(mode="json")
    if existing_orm:
        existing_orm.config_json = config_json
        existing_orm.step_count = len(orch_steps)
        existing_orm.name = seq_config.name
    else:
        db.add(SequenceConfigORM(
            id=sequence_id,
            name=seq_config.name,
            owner_user_id=current_user.id if current_user else "system",
            is_shared=False,
            config_json=config_json,
            step_count=len(orch_steps),
        ))
    db.flush()

    # 6. Register in memory
    register_sequence_config(seq_config)

    # 7. Query eligible ConferenceLeads
    leads = (
        db.query(ConferenceLead)
        .filter(
            ConferenceLead.conference_name == req.conference_name,
            ConferenceLead.outreach_type == "pre",
            ConferenceLead.linkedin_url.isnot(None),
            ConferenceLead.linkedin_url != "",
            ConferenceLead.generated_sequence.isnot(None),
        )
    )
    if current_user:
        leads = leads.filter(ConferenceLead.user_id == current_user.id)
    leads = leads.all()

    enrolled = 0
    skipped = 0
    now = datetime.now(timezone.utc)
    first_step = orch_steps[0] if orch_steps else None

    from ...db.models import Prospect, ProspectStatus

    for lead in leads:
        # 8. Check if already enrolled
        existing = (
            db.query(OrchSequenceStateORM)
            .filter_by(lead_id=lead.id)
            .first()
        )
        if existing:
            skipped += 1
            continue

        # 9. Ensure a Prospect row exists for this conference lead
        #    (OrchSequenceState.lead_id has FK to prospects.id)
        prospect = db.query(Prospect).filter_by(id=lead.id).first()
        if not prospect:
            real_email = lead.email if (lead.email and "@placeholder.local" not in lead.email) else ""
            prospect = Prospect(
                id=lead.id,
                user_id=current_user.id if current_user else None,
                first_name=lead.first_name or "",
                last_name=lead.last_name or "",
                email=real_email or f"conf-{lead.id[:8]}@placeholder.local",
                title=lead.title,
                company=lead.company,
                linkedin_url=lead.linkedin_url,
                status=ProspectStatus.enrolled,
            )
            db.add(prospect)
            db.flush()

        # 10. Create OrchSequenceStateORM row
        state_id = str(uuid.uuid4())
        scheduled_at = now + timedelta(days=first_step.day_offset) if first_step else now
        db.add(OrchSequenceStateORM(
            state_id=state_id,
            lead_id=lead.id,
            user_id=current_user.id if current_user else None,
            sequence_id=sequence_id,
            status=OrchestrationStatus.active.value,
            current_step_index=0,
            enrolled_at=now,
            scheduled_next_at=scheduled_at,
        ))
        enrolled += 1

    try:
        db.commit()
    except Exception as commit_err:
        db.rollback()
        logger.error("launch-linkedin commit error: %s", commit_err, exc_info=True)
        raise HTTPException(500, f"Failed to enroll leads: {commit_err}")

    logger.info(
        "launch-linkedin: enrolled=%d skipped=%d eligible=%d seq=%s",
        enrolled, skipped, len(leads), sequence_id,
    )
    return {
        "enrolled": enrolled,
        "skipped": skipped,
        "total_eligible": len(leads),
        "sequence_id": sequence_id,
        "linkedin_steps": len(orch_steps),
    }
