"""
Campaign management API — CRUD, AI message generation, launch, pause, cancel, and analytics.

This is one of the most critical files in the system. It implements the full
campaign lifecycle:

  1. CREATE  — POST /campaigns/ with goal, value prop, audience
  2. PREPARE — POST /campaigns/{id}/prepare creates EmailSequence rows with LinkedIn content
  3. GENERATE — POST /campaigns/{id}/generate-sample uses Claude to write personalised messages
  4. REFINE  — PATCH /campaigns/{id}/refine-message iterates on a message with user feedback
  5. APPLY   — POST /campaigns/{id}/apply-to-all clones approved style to all prospects
  6. PREVIEW — GET /campaigns/{id}/preview returns generated sequences for review
  7. APPROVE — POST /campaigns/{id}/approve-all marks all sequences as approved
  8. LAUNCH  — POST /campaigns/{id}/launch enrolls prospects into LinkedIn orchestration
  9. PAUSE   — POST /campaigns/{id}/pause pauses orchestration (can be resumed)
  10. CANCEL — POST /campaigns/{id}/cancel hard-stops everything (non-resumable)

Launch flow (the most complex part):
  - Validates all approved sequences are not suppressed
  - Resolves the sequence config (pinned per-campaign or legacy global fallback)
  - Validates the LinkedIn-only step structure
  - Marks sequences as "enrolled" and prospects as "enrolled"
  - Creates orch_sequence_states rows via the state machine
  - The orchestration scheduler (main.py) then advances these states over time

Multi-tenancy: all endpoints enforce user_id ownership. A user can only see/modify
their own campaigns. Ownership check returns 404 (not 403) to avoid leaking existence.

Related files:
  - src/orchestration/       — state machine, scheduler, handlers that execute steps
  - src/services/linkedin_worker.py — drains the linkedin_queue written by handlers
  - src/api/settings.py      — writing style and copy rules used in AI generation
  - src/services/email_gen_helpers.py — sanitisation and JSON parsing for AI responses
  - sales_copy_principles.md — sales copy guidelines injected into AI prompts
"""

import json
import os
import threading
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, case
from sqlalchemy.orm import Session, joinedload
from ..db.database import get_db
from ..db.models import (
    Campaign, CampaignStatus, EmailSequence, Prospect,
    SendLog, ProspectStatus, SequenceStatus, AppSetting
)
from ..config import (
    PROSPECT_CAP, INVALID_EMAIL_STATUSES,
    DEFAULT_LINKEDIN_CONNECT_DAY,
    DEFAULT_LINKEDIN_MESSAGE_DAY,
)
from ..db.models import User
from .auth import get_current_user
router = APIRouter(prefix="/campaigns", tags=["campaigns"])
logger = logging.getLogger(__name__)


# ── Launch concurrency control ───────────────────────────────────────────────
# Per-campaign launch lock prevents two concurrent launch_campaign requests
# from double-enrolling prospects into LinkedIn orchestration. This happens
# when an operator spam-clicks the "Launch" button in the dashboard. The lock
# is non-blocking so the second click gets an immediate 409 Conflict.
# Locks are evicted after use to prevent unbounded memory growth.
_LAUNCH_LOCKS: dict[str, threading.Lock] = {}
_LAUNCH_LOCKS_GUARD = threading.Lock()  # protects the _LAUNCH_LOCKS dict itself


def _get_launch_lock(campaign_id: str) -> threading.Lock:
    """Get or create a per-campaign lock. Thread-safe via _LAUNCH_LOCKS_GUARD."""
    with _LAUNCH_LOCKS_GUARD:
        lock = _LAUNCH_LOCKS.get(campaign_id)
        if lock is None:
            lock = threading.Lock()
            _LAUNCH_LOCKS[campaign_id] = lock
        return lock


# ── Request/Response models ──────────────────────────────────────────────────

class CampaignCreateRequest(BaseModel):
    """Request body for POST /campaigns/. Either segment_id or prospect_ids
    must be provided to define the campaign audience."""
    name: str
    goal: str                                        # e.g. "Book meetings with HSE directors in GCC O&G"
    value_prop: Optional[str] = ""                    # e.g. "AI safety monitoring reduces incidents 40%"
    segment_id: Optional[str] = None                 # target an ICP segment (mutually exclusive with prospect_ids)
    prospect_ids: Optional[list[str]] = None         # target specific prospects by ID
    sending_limit_per_day: int = Field(default=50)   # daily cap for orchestration scheduler
    ab_test_enabled: bool = False                    # enable A/B variant for email1 subject line
    # Pin this campaign to a specific sequence config (step types + day offsets).
    # NULL keeps the legacy behaviour: launch falls back to the global
    # custom_sequence_config app_setting or the hardcoded default.
    sequence_config_id: Optional[str] = None
    # Multi-account LinkedIn: which LinkedIn account sends outreach for this campaign.
    # NULL = use the default/legacy global PhantomBuster configuration.
    outreach_account_id: Optional[str] = None
    scrape_account_id: Optional[str] = None


class CampaignUpdateRequest(BaseModel):
    name: Optional[str] = None
    goal: Optional[str] = None
    value_prop: Optional[str] = None
    segment_id: Optional[str] = None
    prospect_ids: Optional[list[str]] = None
    sending_limit_per_day: Optional[int] = None
    ab_test_enabled: Optional[bool] = None
    sequence_config_id: Optional[str] = None
    # Multi-account LinkedIn: which LinkedIn account sends outreach / scrapes leads.
    # NULL clears the field (fall back to env config).
    outreach_account_id: Optional[str] = None
    scrape_account_id: Optional[str] = None


class SequenceEditRequest(BaseModel):
    linkedin_connect_note: Optional[str] = None
    linkedin_message: Optional[str] = None
    approved: Optional[bool] = None


# ── Helper functions ─────────────────────────────────────────────────────────

def _setting(db: Session, key: str, default: str) -> str:
    """Read a single value from the app_settings key-value store."""
    row = db.query(AppSetting).filter_by(key=key).first()
    return row.value if row else default


def _set_orch_states_for_campaign(db: Session, campaign, target_status: str, reason: str) -> int:
    """Bulk-transition orchestration states for all prospects in a campaign.

    Used by pause/resume/cancel to keep the LinkedIn scheduling state machine
    in sync with the campaign status. Without this, pausing a campaign would
    not actually stop the orchestration scheduler from advancing its prospects.

    Args:
        target_status: "paused", "active", or "stopped_cancelled"
        reason: human-readable string stored in paused_reason column

    Returns count of orch_sequence_states rows modified.
    """
    try:
        from ..orchestration.orm import OrchSequenceStateORM
        prospect_ids = [
            pid for (pid,) in
            db.query(EmailSequence.prospect_id).filter_by(campaign_id=campaign.id).all()
        ]
        if not prospect_ids:
            return 0

        if target_status == "paused":
            active_statuses = ("active", "waiting_for_next_step")
        elif target_status == "active":
            active_statuses = ("paused",)
        elif target_status == "stopped_cancelled":
            active_statuses = ("active", "waiting_for_next_step", "paused")
        else:
            return 0

        states = db.query(OrchSequenceStateORM).filter(
            OrchSequenceStateORM.lead_id.in_(prospect_ids),
            OrchSequenceStateORM.status.in_(active_statuses),
        ).all()

        for s in states:
            s.status = target_status
            if target_status == "paused":
                s.paused_reason = reason
            elif target_status == "active":
                s.paused_reason = None
        return len(states)
    except Exception as exc:
        logger.warning("_set_orch_states_for_campaign: %s", exc)
        return 0


def _get_campaign(campaign_id: str, db: Session, current_user: "User | None") -> Campaign:
    """
    Fetch a campaign with fail-closed ownership check (FIX-13).

    Returns 404 (not 403) on ownership mismatch so we don't leak the existence
    of campaigns the caller doesn't own.
    """
    campaign = db.query(Campaign).filter_by(id=campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if current_user is None:
        if db.query(User).count() > 0:
            raise HTTPException(status_code=401, detail="Authentication required.")
        return campaign
    if campaign.user_id is None or campaign.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return campaign


@router.post("/")
def create_campaign(req: CampaignCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="Campaign name is required.")
    if len(req.name) > 200:
        raise HTTPException(status_code=400, detail="Campaign name must be 200 characters or fewer.")
    # Validate prospect_ids if provided  -  must be a list of strings that exist in the DB
    prospect_ids = req.prospect_ids or []
    if prospect_ids:
        if not isinstance(prospect_ids, list) or any(not isinstance(i, str) for i in prospect_ids):
            raise HTTPException(status_code=422, detail="prospect_ids must be a list of ID strings.")
        existing_count = db.query(Prospect).filter(Prospect.id.in_(prospect_ids), Prospect.user_id == current_user.id).count()
        if existing_count != len(prospect_ids):
            raise HTTPException(
                status_code=422,
                detail=f"Only {existing_count} of {len(prospect_ids)} prospect_ids exist in the database.",
            )

    # If a sequence_config_id was provided, validate ownership/sharing
    # before writing it on the Campaign row. A private config owned by
    # another user is NOT usable.
    if req.sequence_config_id:
        from ..db.models import SequenceConfigORM
        sc_row = (
            db.query(SequenceConfigORM)
            .filter_by(id=req.sequence_config_id)
            .first()
        )
        if not sc_row:
            raise HTTPException(status_code=404, detail="Sequence config not found.")
        if sc_row.owner_user_id != (current_user.id if current_user else None) and not sc_row.is_shared:
            raise HTTPException(status_code=403, detail="This sequence config is private.")

    # Validate outreach_account_id / scrape_account_id if provided
    from ..db.models import LinkedInAccount
    for _acct_field, _acct_val in [("outreach_account_id", req.outreach_account_id), ("scrape_account_id", req.scrape_account_id)]:
        if _acct_val:
            _acct = db.query(LinkedInAccount).filter_by(id=_acct_val, is_active=True).first()
            if not _acct:
                raise HTTPException(status_code=404, detail=f"LinkedIn account not found for {_acct_field}.")

    campaign = Campaign(
        id=str(uuid.uuid4()),
        user_id=current_user.id if current_user else None,
        name=req.name,
        goal=req.goal,
        value_prop=req.value_prop,
        segment_id=req.segment_id,
        prospect_ids=prospect_ids,
        sending_limit_per_day=req.sending_limit_per_day,
        ab_test_enabled=req.ab_test_enabled,
        sequence_config_id=req.sequence_config_id,
        outreach_account_id=req.outreach_account_id,
        scrape_account_id=req.scrape_account_id,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return _campaign_to_dict(campaign)


@router.get("/")
def list_campaigns(
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
    include_archived: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Campaign)
    if current_user:
        q = q.filter(Campaign.user_id == current_user.id)
    if status:
        # Filter by specific status if provided
        q = q.filter(Campaign.status == status)
    elif not include_archived:
        # By default, exclude archived campaigns
        q = q.filter(Campaign.status != CampaignStatus.archived)
    campaigns = (
        q.order_by(Campaign.created_at.desc())
        .offset(offset)
        .limit(min(limit, 500))
        .all()
    )
    return [_campaign_to_dict(c) for c in campaigns]


@router.get("/{campaign_id}")
def get_campaign(campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    campaign = _get_campaign(campaign_id, db, current_user)
    return _campaign_to_dict(campaign)


@router.patch("/{campaign_id}")
def update_campaign(campaign_id: str, req: CampaignUpdateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    campaign = _get_campaign(campaign_id, db, current_user)
    if req.name is not None:
        campaign.name = req.name
    if req.goal is not None:
        campaign.goal = req.goal
    if req.value_prop is not None:
        campaign.value_prop = req.value_prop
    if req.segment_id is not None:
        campaign.segment_id = req.segment_id if req.segment_id else None
    if req.prospect_ids is not None:
        if req.prospect_ids:
            # FIX-16: scope existence query to prospects owned by the caller  -
            # otherwise a rep could absorb another user's prospects by passing
            # their IDs.
            owner_q = db.query(Prospect.id).filter(Prospect.id.in_(req.prospect_ids))
            if current_user:
                owner_q = owner_q.filter(Prospect.user_id == current_user.id)
            existing = {p.id for p in owner_q.all()}
            missing = set(req.prospect_ids) - existing
            if missing:
                raise HTTPException(status_code=422, detail=f"{len(missing)} prospect IDs not found.")
        campaign.prospect_ids = req.prospect_ids
    if req.sending_limit_per_day is not None:
        campaign.sending_limit_per_day = req.sending_limit_per_day
    if req.ab_test_enabled is not None:
        campaign.ab_test_enabled = req.ab_test_enabled
    if req.sequence_config_id is not None:
        # Allow changing the sequence only while the campaign is still
        # a draft  -  once it's live, swapping mid-flight would diverge
        # the orch state from the sequence and cause
        # silent drift.
        if campaign.status not in (CampaignStatus.draft,):
            raise HTTPException(
                status_code=409,
                detail="Sequence can only be changed while the campaign is still a draft.",
            )
        if req.sequence_config_id == "":
            campaign.sequence_config_id = None
        else:
            from ..db.models import SequenceConfigORM
            sc_row = (
                db.query(SequenceConfigORM)
                .filter_by(id=req.sequence_config_id)
                .first()
            )
            if not sc_row:
                raise HTTPException(status_code=404, detail="Sequence config not found.")
            if sc_row.owner_user_id != (current_user.id if current_user else None) and not sc_row.is_shared:
                raise HTTPException(status_code=403, detail="This sequence config is private.")
            campaign.sequence_config_id = req.sequence_config_id
    # Multi-account LinkedIn: update outreach/scrape account assignments.
    # Only allowed while the campaign is still a draft (same rationale as sequence_config_id).
    from ..db.models import LinkedInAccount
    if req.outreach_account_id is not None:
        if campaign.status not in (CampaignStatus.draft,):
            raise HTTPException(status_code=409, detail="Outreach account can only be changed while the campaign is still a draft.")
        if req.outreach_account_id == "" or req.outreach_account_id is None:
            campaign.outreach_account_id = None
        else:
            _acct = db.query(LinkedInAccount).filter_by(id=req.outreach_account_id, is_active=True).first()
            if not _acct:
                raise HTTPException(status_code=404, detail="LinkedIn account not found for outreach_account_id.")
            campaign.outreach_account_id = req.outreach_account_id
    if req.scrape_account_id is not None:
        if campaign.status not in (CampaignStatus.draft,):
            raise HTTPException(status_code=409, detail="Scrape account can only be changed while the campaign is still a draft.")
        if req.scrape_account_id == "" or req.scrape_account_id is None:
            campaign.scrape_account_id = None
        else:
            _acct = db.query(LinkedInAccount).filter_by(id=req.scrape_account_id, is_active=True).first()
            if not _acct:
                raise HTTPException(status_code=404, detail="LinkedIn account not found for scrape_account_id.")
            campaign.scrape_account_id = req.scrape_account_id
    db.commit()
    return _campaign_to_dict(campaign)


@router.get("/{campaign_id}/contacts")
def get_campaign_contacts(
    campaign_id: str,
    offset: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns prospects assigned to this campaign with pagination."""
    campaign = _get_campaign(campaign_id, db, current_user)
    limit = min(limit, 500)

    if campaign.prospect_ids:
        q = db.query(Prospect).filter(Prospect.id.in_(campaign.prospect_ids))
    elif campaign.segment_id:
        q = db.query(Prospect).filter_by(segment_id=campaign.segment_id)
    else:
        q = db.query(Prospect).filter(False)  # empty

    total = q.count()
    prospects = q.order_by(Prospect.created_at).offset(offset).limit(limit).all()

    return {
        "campaign_id": campaign_id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "prospects": [
            {
                "id": p.id,
                "first_name": p.first_name,
                "last_name": p.last_name or "",
                "email": p.email,
                "title": p.title or "",
                "company": p.company or "",
                "status": p.status.value if p.status else "pending",
                "email_validation_status": p.email_validation_status,
            }
            for p in prospects
        ],
    }


@router.get("/{campaign_id}/preview")
def preview_sequences(
    campaign_id: str,
    offset: int = 0,
    limit: int = 1000,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns generated email sequences for preview with pagination.

    Query params:
      offset: skip first N sequences (default 0)
      limit:  max sequences to return (default 1000, max 2000)
    """
    _get_campaign(campaign_id, db, current_user)  # ownership check
    limit = min(limit, 2000)
    sequences = (
        db.query(EmailSequence)
        .filter(EmailSequence.campaign_id == campaign_id)
        .options(joinedload(EmailSequence.prospect))
        .order_by(EmailSequence.created_at)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_sequence_to_dict(s) for s in sequences]


class LinkedInPrepareRequest(BaseModel):
    linkedin_connect_note: Optional[str] = None
    linkedin_message: Optional[str] = None


@router.post("/{campaign_id}/prepare")
def prepare_linkedin_sequences(
    campaign_id: str,
    req: LinkedInPrepareRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create/update EmailSequence rows for all campaign prospects with LinkedIn content."""
    campaign = db.query(Campaign).filter_by(id=campaign_id).first()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if current_user and campaign.user_id != current_user.id:
        raise HTTPException(403, "Not your campaign")

    # Get prospects for this campaign
    prospect_ids = json.loads(campaign.prospect_ids) if isinstance(campaign.prospect_ids, str) else (campaign.prospect_ids or [])
    if not prospect_ids:
        raise HTTPException(422, "No prospects in campaign")

    created = 0
    updated = 0
    for pid in prospect_ids:
        existing = db.query(EmailSequence).filter_by(
            campaign_id=campaign_id, prospect_id=pid
        ).first()
        if existing:
            # Only overwrite content if the request explicitly provides it.
            # This prevents prepare from wiping AI-generated content when
            # called a second time (e.g. after generate-sample).
            if req.linkedin_connect_note is not None:
                existing.linkedin_connect_note = req.linkedin_connect_note
            if req.linkedin_message is not None:
                existing.linkedin_message = req.linkedin_message
            existing.approved = True
            updated += 1
        else:
            seq = EmailSequence(
                id=str(uuid.uuid4()),
                campaign_id=campaign_id,
                prospect_id=pid,
                linkedin_connect_note=req.linkedin_connect_note,
                linkedin_message=req.linkedin_message,
                approved=True,
                status=SequenceStatus.pending,
            )
            db.add(seq)
            created += 1
    db.commit()
    return {"created": created, "updated": updated, "total": created + updated}


# Fallback day offsets when inferring sequence shape from EmailSequence content
# (used by the dashboard's "30-Day Journey" visualisation)
_DEFAULT_LI_CONNECT_DAY = DEFAULT_LINKEDIN_CONNECT_DAY  # day 4
_DEFAULT_LI_MESSAGE_DAY = DEFAULT_LINKEDIN_MESSAGE_DAY  # day 16


def _resolve_campaign_sequence_shape(campaign, db) -> list[dict]:
    """
    Return the step shape this campaign was actually launched with, as a
    list of ``{day, step_type, label}`` dicts in chronological order.

    Resolution order:
      1. ``campaign.sequence_config_id`` → SequenceConfigORM row (the
         pinned per-campaign config introduced by the 0009 migration).
      2. Inferred from the first EmailSequence row for the campaign  -
         count populated ``email{n}`` + LinkedIn content. Day offsets
         fall back to EXAMPLE_DEFAULT_SEQUENCE values so the monitor
         UI still renders something sensible.
      3. Empty list (campaign never generated).

    This is load-bearing for the per-campaign monitor: the dashboard
    was previously reading the workspace-wide legacy
    ``custom_sequence_config`` to build the "30-Day Journey" step dots,
    which meant saving a NEW global sequence silently changed how
    every already-launched campaign rendered. Resolving per-campaign
    fixes that regression.
    """
    from ..db.models import SequenceConfigORM  # local import  -  avoids cycle

    # 1. Pinned config
    scid = getattr(campaign, "sequence_config_id", None)
    if scid:
        row = db.query(SequenceConfigORM).filter_by(id=scid).first()
        if row and row.config_json:
            try:
                raw = row.config_json if isinstance(row.config_json, dict) else json.loads(row.config_json)
                out: list[dict] = []
                e_num = 0
                for s in raw.get("steps", []):
                    st = s.get("step_type")
                    day = int(s.get("day_offset") or 0)
                    if st == "send_email":
                        e_num += 1
                        out.append({"day": day, "step_type": "email", "label": f"Email {e_num}"})
                    elif st == "send_linkedin_connect":
                        out.append({"day": day, "step_type": "linkedin_connect", "label": s.get("label") or "LinkedIn Connect"})
                    elif st == "send_linkedin_message":
                        out.append({"day": day, "step_type": "linkedin_message", "label": s.get("label") or "LinkedIn Message"})
                if out:
                    config_max_day = max((int(s.get("day_offset") or 0) for s in raw.get("steps", [])), default=30)
                    out.sort(key=lambda x: x["day"])
                    # Attach total duration from original config
                    if out:
                        out[-1]["_sequence_total_days"] = config_max_day
                    return out
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not parse sequence_configs row %s: %s", scid, exc)

    # 2. Infer from first generated sequence row (LinkedIn fields only).
    sample = (
        db.query(EmailSequence)
        .filter(EmailSequence.campaign_id == campaign.id)
        .first()
    )
    if sample is None:
        return []

    has_li_connect = bool((sample.linkedin_connect_note or "").strip())
    has_li_message = bool((sample.linkedin_message or "").strip())

    shape: list[dict] = []
    if has_li_connect:
        shape.append({"day": _DEFAULT_LI_CONNECT_DAY, "step_type": "linkedin_connect", "label": "LinkedIn Connect"})
    if has_li_message:
        shape.append({"day": _DEFAULT_LI_MESSAGE_DAY, "step_type": "linkedin_message", "label": "LinkedIn Message"})

    shape.sort(key=lambda x: x["day"])
    return shape


@router.get("/{campaign_id}/jobs")
def campaign_jobs(campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List jobs for a campaign (most recent first). Used by the dashboard
    to detect if generation is still running and show a progress overlay."""
    _get_campaign(campaign_id, db, current_user)  # ownership check
    from ..db.models import Job
    from sqlalchemy import cast, String
    jobs = (
        db.query(Job)
        .filter(cast(Job.payload["campaign_id"], String) == campaign_id)
        .order_by(Job.created_at.desc())
        .limit(5)
        .all()
    )
    # Fallback: if JSON filter returns nothing, try string match
    if not jobs:
        all_jobs = db.query(Job).order_by(Job.created_at.desc()).limit(20).all()
        import json as _json
        jobs = []
        for j in all_jobs:
            try:
                p = _json.loads(j.payload) if isinstance(j.payload, str) else j.payload
                if p and p.get("campaign_id") == campaign_id:
                    jobs.append(j)
            except Exception:
                pass
        jobs = jobs[:5]
    return [
        {
            "id": j.id,
            "type": j.type,
            "status": j.status,
            "progress": j.progress,
            "progress_detail": j.progress_detail,
            "error": j.error,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in jobs
    ]


@router.get("/{campaign_id}/stats")
def campaign_stats(campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Per-step send counts and per-lead status breakdown for a campaign."""
    campaign = _get_campaign(campaign_id, db, current_user)

    sent_statuses = [
        SequenceStatus.enrolled.value,
        SequenceStatus.opened.value,
        SequenceStatus.replied.value,
        SequenceStatus.bounced.value,
        SequenceStatus.unsubscribed.value,
        SequenceStatus.connected.value,
        SequenceStatus.messaged.value,
    ]

    row = db.query(
        func.count().label("total"),
        func.sum(case((EmailSequence.status.in_(sent_statuses), 1), else_=0)).label("enrolled"),
        func.sum(case((EmailSequence.replied_at.isnot(None), 1), else_=0)).label("replied"),
    ).filter(EmailSequence.campaign_id == campaign_id).one()

    total    = row.total    or 0
    enrolled = row.enrolled or 0
    replied  = row.replied  or 0

    # LinkedIn stats — actual queue counts, not just "has content"
    from ..db.models import LinkedInQueue, LinkedInQueueStatus
    # Only count successful sends — failed connects are shown separately in the "Failed" stat
    li_connect_sent = db.query(func.count()).filter(
        LinkedInQueue.campaign_id == campaign_id,
        LinkedInQueue.action_type == "connect",
        LinkedInQueue.status == LinkedInQueueStatus.done,
    ).scalar() or 0
    li_message_sent = db.query(func.count()).filter(
        LinkedInQueue.campaign_id == campaign_id,
        LinkedInQueue.action_type == "message",
        LinkedInQueue.status == LinkedInQueueStatus.done,
    ).scalar() or 0

    # Count open orchestration tasks for this campaign
    open_tasks = 0
    try:
        from ..orchestration.orm import OrchSequenceStateORM
        prospect_ids = [
            pid for (pid,) in
            db.query(EmailSequence.prospect_id).filter_by(campaign_id=campaign_id).all()
        ]
        if prospect_ids:
            open_tasks = db.query(func.count()).filter(
                OrchSequenceStateORM.lead_id.in_(prospect_ids),
                OrchSequenceStateORM.status.in_(("active", "waiting_for_next_step")),
            ).scalar() or 0
    except Exception:
        pass

    shape = _resolve_campaign_sequence_shape(campaign, db)

    result = {
        "total": total,
        "enrolled": enrolled,
        "replied": replied,
        "li_connect_sent": li_connect_sent,
        "li_message_sent": li_message_sent,
        "open_tasks": open_tasks,
        "sequence_shape": shape,
    }

    # Audit 2026-05-06: include active generation job so the dashboard
    # can show a progress overlay and block launch while generating.
    try:
        import json as _stats_json
        _JobModel = db.execute(
            __import__("sqlalchemy").text(
                "SELECT id, status, progress, progress_detail, payload FROM jobs "
                "WHERE status IN ('running','pending') ORDER BY created_at DESC LIMIT 10"
            )
        ).fetchall()
        active_gen = None
        for row in _JobModel:
            try:
                p = _stats_json.loads(row[4]) if isinstance(row[4], str) else row[4]
                if p and p.get("campaign_id") == campaign_id:
                    active_gen = {
                        "id": row[0],
                        "status": row[1],
                        "progress": row[2],
                        "progress_detail": row[3],
                    }
                    break
            except Exception:
                pass
        result["active_job"] = active_gen
    except Exception:
        result["active_job"] = None

    return result


@router.patch("/{campaign_id}/sequences/{sequence_id}")
def edit_sequence(
    campaign_id: str,
    sequence_id: str,
    req: SequenceEditRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_campaign(campaign_id, db, current_user)  # ownership check
    seq = db.query(EmailSequence).filter_by(id=sequence_id, campaign_id=campaign_id).first()
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found.")

    # Block edits on sequences that have already been sent
    non_editable_statuses = [
        SequenceStatus.enrolled, SequenceStatus.opened,
        SequenceStatus.replied, SequenceStatus.bounced,
        SequenceStatus.unsubscribed,
        SequenceStatus.connected, SequenceStatus.messaged,
    ]
    if seq.status in non_editable_statuses:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot edit a sequence that has already been sent. Status: {seq.status.value}",
        )

    content_fields = ["linkedin_connect_note", "linkedin_message"]
    content_changed = any(getattr(req, f) is not None for f in content_fields)

    if req.linkedin_connect_note is not None:
        seq.linkedin_connect_note = req.linkedin_connect_note
    if req.linkedin_message is not None:
        seq.linkedin_message = req.linkedin_message

    if req.approved is not None:
        seq.approved = req.approved
    elif content_changed:
        seq.approved = False

    db.commit()
    db.refresh(seq)
    return _sequence_to_dict(seq)


@router.post("/{campaign_id}/approve-all")
def approve_all_sequences(campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _get_campaign(campaign_id, db, current_user)  # ownership check
    sequences = db.query(EmailSequence).filter_by(campaign_id=campaign_id).all()
    for seq in sequences:
        seq.approved = True
    db.commit()
    return {"approved": len(sequences)}


@router.delete("/{campaign_id}/sequences")
def clear_sequences(
    campaign_id: str,
    confirm: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Deletes all email sequences for a campaign so they can be re-generated.
    Only allowed when the campaign is in draft or paused status (not active).
    Requires confirm=true if the campaign has been launched.
    """
    campaign = _get_campaign(campaign_id, db, current_user)
    if campaign.status == CampaignStatus.active:
        raise HTTPException(
            status_code=400,
            detail="Cannot clear sequences on an active campaign. Pause it first."
        )
    seq_count = db.query(EmailSequence).filter_by(campaign_id=campaign_id).count()
    if campaign.launched_at and not confirm:
        raise HTTPException(
            status_code=409,
            detail="Campaign '{}' was launched and has {} sequences. Pass confirm=true to clear.".format(
                campaign.name, seq_count,
            ),
        )
    deleted = db.query(EmailSequence).filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    db.commit()
    logger.warning("clear_sequences: cleared %d sequences from campaign %s (%s) by user %s",
                    deleted, campaign_id, campaign.name, current_user.id)
    return {"deleted": deleted}


@router.delete("/{campaign_id}/sequences/{sequence_id}")
def delete_single_sequence(campaign_id: str, sequence_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Delete a single email sequence from a campaign."""
    _get_campaign(campaign_id, db, current_user)
    seq = db.query(EmailSequence).filter_by(id=sequence_id, campaign_id=campaign_id).first()
    if not seq:
        raise HTTPException(404, "Sequence not found.")
    db.delete(seq)
    db.commit()
    logger.info(f"Deleted sequence {sequence_id} from campaign {campaign_id}.")
    return {"deleted": 1, "sequence_id": sequence_id}


@router.post("/{campaign_id}/launch")
def launch_campaign(campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Enrolls all approved sequences into LinkedIn orchestration via PhantomBuster.

    Hardening 2.4: per-campaign launch lock prevents two concurrent
    launches from double-enrolling prospects. A spam-click on the
    dashboard "Launch" button gets an immediate 409 instead of two
    serialised launches with the same leads enrolled twice.
    """
    lock = _get_launch_lock(campaign_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail=(
                "A launch is already in progress for this campaign. "
                "Wait for it to finish before retrying."
            ),
        )
    try:
        return _launch_campaign_locked(campaign_id, db, current_user)
    finally:
        lock.release()
        # Evict the lock after the operation completes to prevent unbounded growth.
        with _LAUNCH_LOCKS_GUARD:
            _LAUNCH_LOCKS.pop(campaign_id, None)


def _validate_linkedin_only_steps(seq_config) -> None:
    """FIX-4: minimal pre-flight for the LinkedIn-only launch path.

    The email-heavy hardening block that follows the linkedin_only short-
    circuit used to be the only launch validation. LinkedIn-only launches
    bypassed it entirely, so a corrupt sequence with duplicate steps,
    negative delays, or zero LinkedIn steps would silently "launch" and
    then never do anything.

    Raises ``HTTPException(422)`` on failure.
    """
    from ..orchestration.enums import StepType

    steps = list(getattr(seq_config, "steps", []) or [])
    if not steps:
        raise HTTPException(
            status_code=422,
            detail="Cannot launch: LinkedIn-only sequence has no steps.",
        )

    # Must contain at least one LinkedIn action.
    linkedin_types = {StepType.send_linkedin_connect, StepType.send_linkedin_message}
    if not any(getattr(s, "step_type", None) in linkedin_types for s in steps):
        raise HTTPException(
            status_code=422,
            detail="Cannot launch: LinkedIn-only sequence has no LinkedIn steps.",
        )

    # Non-negative delays (and a sanity cap on cumulative span).
    cumulative = 0
    for idx, s in enumerate(steps):
        d = getattr(s, "delay_days", None)
        if d is None:
            d = getattr(s, "delay", None)
        if d is None:
            continue
        try:
            d_int = int(d)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail=f"Cannot launch: step {idx + 1} has non-numeric delay.",
            )
        if d_int < 0:
            raise HTTPException(
                status_code=422,
                detail=f"Cannot launch: step {idx + 1} has negative delay ({d_int}).",
            )
        cumulative += d_int
    if cumulative > 120:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot launch: LinkedIn-only sequence spans {cumulative} days "
                f"(must be 0..120). Likely a corrupted sequence config."
            ),
        )

    # Duplicate-step detection: identical (type, day_offset, body-ish) twice in a row.
    def _sig(s):
        return (
            getattr(s, "step_type", None),
            getattr(s, "day_offset", None) or getattr(s, "delay_days", None) or getattr(s, "delay", None),
            (getattr(s, "body", "") or "")[:200],
        )
    for i in range(len(steps) - 1):
        if _sig(steps[i]) == _sig(steps[i + 1]):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Cannot launch: steps {i + 1} and {i + 2} are duplicates. "
                    f"Likely a sequence-config corruption."
                ),
            )


def _launch_campaign_locked(campaign_id: str, db: Session, current_user: User):
    """The actual launch body — runs only inside the per-campaign lock.

    This is the most complex endpoint in the system. It:
      1. Checks campaign status (already active? paused? → handle those cases)
      2. Loads all approved sequences with their prospects
      3. Pre-flight: checks suppression list + excluded domains
      4. Resolves the sequence config (per-campaign pinned → global → hardcoded default)
      5. Validates LinkedIn-only step structure (no duplicates, sane delays, etc.)
      6. Marks sequences/prospects as "enrolled"
      7. Creates orch_sequence_states via _enroll_prospects_into_linkedin_orch
      8. On failure: rolls back to draft status
    """
    campaign = _get_campaign(campaign_id, db, current_user)

    # Initialise so the result dict can reference it even on the
    # resume / linkedin-only short-circuit paths below.
    linkedin_enrolled = 0

    if campaign.status == CampaignStatus.active:
        raise HTTPException(status_code=400, detail="Campaign is already active.")

    if campaign.status == CampaignStatus.cancelled:
        raise HTTPException(status_code=400, detail="Campaign is cancelled and cannot be relaunched.")

    # Paused campaigns are resumed by setting status back to active and
    # resuming the LinkedIn orchestration states.
    if campaign.status == CampaignStatus.paused:
        campaign.status = CampaignStatus.active
        _set_orch_states_for_campaign(db, campaign, "active", "campaign_resumed")
        db.commit()
        return {
            "campaign_id": campaign_id,
            "leads_enrolled": 0,
            "status": "active",
            "note": "Resumed paused campaign.",
        }

    approved_sequences = (
        db.query(EmailSequence)
        .filter(EmailSequence.campaign_id == campaign_id, EmailSequence.approved == True)
        .options(joinedload(EmailSequence.prospect))
        .all()
    )

    if not approved_sequences:
        raise HTTPException(
            status_code=400,
            detail="No approved sequences found. Preview and approve emails before launching."
        )

    # Pre-flight suppression check.
    from ..db.models import UnsubscribeEntry, ExcludedDomain
    all_suppressed = {u.email.lower() for u in db.query(UnsubscribeEntry.email).all()}
    all_excluded_domains = {d.domain.lower() for d in db.query(ExcludedDomain.domain).all()}
    suppressed_in_batch = []
    for seq in approved_sequences:
        if seq.prospect:
            email = seq.prospect.email.lower()
            domain = email.split('@')[-1] if '@' in email else ''
            if email in all_suppressed or domain in all_excluded_domains:
                suppressed_in_batch.append(seq.prospect.email)
    if suppressed_in_batch:
        sample = suppressed_in_batch[:5]
        more = f" (and {len(suppressed_in_batch) - 5} more)" if len(suppressed_in_batch) > 5 else ""
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot launch: {len(suppressed_in_batch)} approved lead(s) "
                f"are on the suppression list and would be silently dropped at "
                f"enrollment. Remove them from the campaign or unsuppress them "
                f"before launching. Affected: {', '.join(sample)}{more}"
            ),
        )

    # ── Resolve sequence config ────────────────────────────────────────────
    # The sequence config defines WHAT steps to execute and WHEN (day offsets).
    # Resolution order (first non-null wins):
    #   1. Campaign.sequence_config_id → SequenceConfigORM row (pinned at create time)
    #   2. Global custom_sequence_config in app_settings (legacy, workspace-wide)
    #   3. Hardcoded "invigilo-linkedin-only-v1" default
    from ..orchestration.config import EXAMPLE_DEFAULT_SEQUENCE, get_sequence_config
    from .settings import CUSTOM_SEQUENCE_CONFIG_KEY

    seq_config = None
    if getattr(campaign, "sequence_config_id", None):
        from ..db.models import SequenceConfigORM
        sc_row = (
            db.query(SequenceConfigORM)
            .filter_by(id=campaign.sequence_config_id)
            .first()
        )
        if sc_row and sc_row.config_json:
            try:
                from ..orchestration.models import SequenceConfig
                seq_config = SequenceConfig(**(sc_row.config_json if isinstance(sc_row.config_json, dict) else json.loads(sc_row.config_json)))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Corrupt sequence_configs row %s referenced by campaign %s: %s  -  "
                    "falling back to legacy global config",
                    campaign.sequence_config_id, campaign.id, exc,
                )
    if not seq_config:
        _custom_cfg_row = db.query(AppSetting).filter_by(key=CUSTOM_SEQUENCE_CONFIG_KEY).first()
        if _custom_cfg_row and _custom_cfg_row.value:
            try:
                from ..orchestration.models import SequenceConfig
                seq_config = SequenceConfig(**json.loads(_custom_cfg_row.value))
            except Exception:
                logger.warning("Corrupt custom_sequence_config in DB, falling back to default")
    if not seq_config:
        try:
            seq_config = get_sequence_config("invigilo-linkedin-only-v1")
        except KeyError:
            seq_config = EXAMPLE_DEFAULT_SEQUENCE

    # All campaigns now run LinkedIn-only via PhantomBuster (email sending via
    # Instantly was removed in March 2026). Validate the step structure before
    # committing to a launch.
    _validate_linkedin_only_steps(seq_config)

    from .unsubscribes import is_suppressed
    from ..services.suppression import is_domain_excluded
    leads_enrolled = 0
    for seq in approved_sequences:
        if not seq.prospect:
            continue
        if is_suppressed(seq.prospect.email, db) or is_domain_excluded(seq.prospect.email, db):
            continue
        seq.status = SequenceStatus.enrolled
        if seq.prospect:
            seq.prospect.status = ProspectStatus.enrolled
        leads_enrolled += 1

    campaign.status = CampaignStatus.active
    campaign.launched_at = datetime.now(timezone.utc)
    db.commit()

    try:
        linkedin_enrolled = _enroll_prospects_into_linkedin_orch(
            campaign=campaign,
            approved_sequences=approved_sequences,
            db=db,
            sequence_config=seq_config,
        )
        logger.info(
            "campaign %s: enrolled %d prospect(s) into LinkedIn orchestration",
            campaign_id, linkedin_enrolled,
        )
    except Exception as li_err:  # noqa: BLE001
        logger.error(
            "campaign %s: LinkedIn orchestration enrollment failed: %s",
            campaign_id, li_err, exc_info=True,
        )
        # Roll back to draft — don't leave the campaign active with no orch states
        campaign.status = CampaignStatus.draft
        db.commit()
        raise HTTPException(
            status_code=500,
            detail=(
                "Campaign launch failed: could not create orchestration states. "
                "Error: {}".format(str(li_err)[:200])
            ),
        )

    return {
        "campaign_id": campaign_id,
        "leads_enrolled": leads_enrolled,
        "linkedin_enrolled": linkedin_enrolled,
        "status": "active",
    }


def _enroll_prospects_into_linkedin_orch(
    campaign: "Campaign",
    approved_sequences: list,
    db: Session,
    sequence_config=None,
) -> int:
    """
    Enrol successfully-enrolled prospects into the LinkedIn-only orchestration
    companion sequence. Idempotent: prospects that already have an active
    orch sequence state are skipped. Prospects without a ``linkedin_url`` are
    skipped (LinkedIn steps would fail at handler time anyway). Non-fatal:
    a single enrollment failure is logged and the loop continues.

    Returns the number of prospects newly enrolled.

    See ``src/orchestration/config.py::EXAMPLE_LINKEDIN_ONLY_SEQUENCE`` for
    the step definition. PhantomBuster dispatches via the linkedin_worker
    drain loop once ``linkedin_queue`` rows are written by the orchestration
    scheduler's ``SendLinkedInConnectHandler`` / ``SendLinkedInMessageHandler``.
    """
    from datetime import timedelta
    from ..orchestration.config import get_sequence_config, register_sequence_config
    from ..orchestration.enums import OrchestrationStatus, StepType
    from ..orchestration.models import LeadSequenceState
    from ..orchestration.repositories import (
        AuditLogRepository,
        LeadSequenceStateRepository,
    )
    from ..orchestration.state_machine import transition_state

    # Use the operator's custom sequence config when provided (the
    # linkedin_only launch path passes its resolved seq_config so this
    # function honors day_offset=0 / custom step layouts instead of
    # falling back to the stock 3-day/6-day companion). When no config
    # is passed (hybrid email+LinkedIn path), fall back to the
    # hardcoded invigilo-linkedin-only-v1 as before.
    if sequence_config is not None and getattr(sequence_config, "steps", None):
        # Filter to LinkedIn-relevant steps only — skip email steps and
        # spurious end markers before the first LinkedIn action.
        linkedin_step_types = {
            StepType.send_linkedin_connect,
            StepType.send_linkedin_message,
        }
        first_linkedin_step = next(
            (s for s in sequence_config.steps if s.step_type in linkedin_step_types),
            None,
        )
        if first_linkedin_step is None:
            # No LinkedIn steps in the custom config  -  nothing to enrol.
            return 0
        config = sequence_config
        first_step = first_linkedin_step
        # Make sure the scheduler can look this config up by id on
        # subsequent ticks  -  register it in the in-memory registry.
        try:
            register_sequence_config(config)
        except Exception as reg_err:  # noqa: BLE001
            logger.warning(
                "Could not register custom sequence config '%s': %s",
                getattr(config, "sequence_id", "?"), reg_err,
            )
        SEQUENCE_ID = config.sequence_id
    else:
        SEQUENCE_ID = "invigilo-linkedin-only-v1"
        try:
            config = get_sequence_config(SEQUENCE_ID)
        except KeyError:
            logger.error(
                "LinkedIn-only sequence '%s' not registered  -  skipping orchestration enrollment",
                SEQUENCE_ID,
            )
            return 0
        if not config.steps:
            return 0
        first_step = config.steps[0]

    state_repo = LeadSequenceStateRepository(db)
    audit_repo = AuditLogRepository(db)

    # Batch-fetch existing active orch states to avoid N+1 per-prospect queries
    from ..orchestration.orm import OrchSequenceStateORM
    from ..orchestration.repositories import _ACTIVE_STATUSES
    _candidate_prospect_ids = [
        seq.prospect_id for seq in approved_sequences
        if seq.status == SequenceStatus.enrolled and seq.prospect_id
    ]
    _existing_active_lead_ids = set()
    if _candidate_prospect_ids:
        _existing_active_lead_ids = {
            row.lead_id
            for row in db.query(OrchSequenceStateORM.lead_id).filter(
                OrchSequenceStateORM.lead_id.in_(_candidate_prospect_ids),
                OrchSequenceStateORM.status.in_(_ACTIVE_STATUSES),
            ).all()
        }

    enrolled = 0
    skipped_no_url = 0
    skipped_already_active = 0
    for seq in approved_sequences:
        if seq.status != SequenceStatus.enrolled:
            continue  # Only enrolled prospects
        prospect = seq.prospect
        if prospect is None:
            continue
        if not getattr(prospect, "linkedin_url", None):
            skipped_no_url += 1
            continue

        # Skip prospects that already have an active orch sequence  -  we
        # don't want to double-enroll into a second LinkedIn sequence.
        if prospect.id in _existing_active_lead_ids:
            skipped_already_active += 1
            continue

        enrolled_at = datetime.now(timezone.utc)
        first_scheduled = enrolled_at + timedelta(days=first_step.day_offset)
        state = LeadSequenceState(
            lead_id=prospect.id,
            sequence_id=SEQUENCE_ID,
            status=OrchestrationStatus.pending,
            enrolled_at=enrolled_at,
            scheduled_next_at=first_scheduled,
        )
        try:
            # pending → active via the state machine (writes an audit log)
            state = transition_state(
                state=state,
                trigger="enrolled",
                event=None,
                audit_repo=audit_repo,
            )
            state_repo.upsert_from_pydantic(state)
            # Backfill the multi-user isolation column. The Pydantic model
            # does not carry user_id, so we patch the ORM row directly.
            row = state_repo.get(state.state_id)
            if row is not None:
                row.user_id = campaign.user_id
            enrolled += 1
        except Exception as enroll_err:  # noqa: BLE001
            logger.warning(
                "LinkedIn enrollment: skipping prospect %s: %s",
                prospect.id, enroll_err,
            )
            continue

    if skipped_no_url or skipped_already_active:
        logger.info(
            "LinkedIn enrollment for campaign %s: enrolled=%d skipped_no_url=%d skipped_already_active=%d",
            campaign.id, enrolled, skipped_no_url, skipped_already_active,
        )
    # 2026-04-10: must commit, not just flush. Both call sites
    # (linkedin_only at line ~1234 and hybrid at line ~1552) return the
    # response without a subsequent commit, and FastAPI's get_db
    # teardown closes the session with no commit  -  meaning a flush-only
    # path would silently roll back every orch_sequence_states row this
    # function just wrote. That's the reason MAw and every earlier
    # launch logged "enrolled N prospect(s)" but had zero rows in the
    # table afterwards.
    try:
        db.commit()
    except Exception as commit_err:  # noqa: BLE001
        logger.error(
            "LinkedIn enrollment commit failed for campaign %s: %s",
            campaign.id, commit_err, exc_info=True,
        )
        db.rollback()
        return 0
    return enrolled


@router.post("/{campaign_id}/pause")
def pause_campaign(campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Pause a campaign — flips local status and pauses LinkedIn orchestration states."""
    campaign = _get_campaign(campaign_id, db, current_user)
    if campaign.status == CampaignStatus.cancelled:
        raise HTTPException(status_code=409, detail="Campaign is cancelled and cannot be paused.")
    if campaign.status != CampaignStatus.active:
        raise HTTPException(status_code=400, detail="Only active campaigns can be paused.")
    campaign.status = CampaignStatus.paused
    _set_orch_states_for_campaign(db, campaign, "paused", "campaign_paused")
    db.commit()
    return {"status": "paused"}


@router.post("/{campaign_id}/cancel")
def cancel_campaign(campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Hard-cancel a campaign.

    Marks local SendLog rows ``cancelled`` and EmailSequence rows
    ``unsubscribed`` so a future re-launch doesn't re-pick the same prospects.
    Flips Campaign.status to ``cancelled`` (non-resumable — resume returns 409).
    Stops LinkedIn orchestration states.
    """
    campaign = _get_campaign(campaign_id, db, current_user)
    if campaign.status == CampaignStatus.cancelled:
        return {
            "ok": True,
            "status": "cancelled",
            "cancelled": True,
            "warning": "Campaign was already cancelled.",
        }

    db.query(SendLog).filter(
        SendLog.campaign_id == campaign_id,
        SendLog.status == "pending",
    ).update({"status": "cancelled"}, synchronize_session=False)

    # Collect prospect IDs before updating sequences so we can reset their status
    cancel_prospect_ids = [
        row[0] for row in db.query(EmailSequence.prospect_id)
        .filter(EmailSequence.campaign_id == campaign_id,
                EmailSequence.status.in_([SequenceStatus.pending, SequenceStatus.enrolled, SequenceStatus.opened]))
        .all()
    ]

    db.query(EmailSequence).filter(
        EmailSequence.campaign_id == campaign_id,
        EmailSequence.status.in_([SequenceStatus.pending, SequenceStatus.enrolled, SequenceStatus.opened]),
    ).update({"status": SequenceStatus.unsubscribed}, synchronize_session=False)

    # Reset prospect statuses - only if they have no sequences in other active campaigns
    if cancel_prospect_ids:
        in_other = {
            row[0] for row in db.query(EmailSequence.prospect_id)
            .join(Campaign, Campaign.id == EmailSequence.campaign_id)
            .filter(EmailSequence.campaign_id != campaign_id,
                    EmailSequence.prospect_id.in_(cancel_prospect_ids),
                    Campaign.status.in_([CampaignStatus.active, CampaignStatus.paused]))
            .all()
        }
        reset_ids = [pid for pid in cancel_prospect_ids if pid not in in_other]
        if reset_ids:
            db.query(Prospect).filter(
                Prospect.id.in_(reset_ids),
                Prospect.status == ProspectStatus.enrolled,
            ).update({"status": ProspectStatus.pending}, synchronize_session=False)

    campaign.status = CampaignStatus.cancelled

    # Stop LinkedIn orchestration states for this campaign
    _set_orch_states_for_campaign(db, campaign, "stopped_cancelled", "campaign_cancelled")

    db.commit()

    logger.warning("cancel_campaign: campaign=%s", campaign_id)
    return {
        "ok": True,
        "status": "cancelled",
        "cancelled": True,
    }


@router.post("/{campaign_id}/resume")
def resume_campaign(campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Resume a paused campaign. Cancelled campaigns are non-resumable (409)."""
    campaign = _get_campaign(campaign_id, db, current_user)
    if campaign.status == CampaignStatus.cancelled:
        raise HTTPException(
            status_code=409,
            detail="Campaign is cancelled. Cancelled campaigns cannot be resumed — create a new campaign instead.",
        )
    if campaign.status != CampaignStatus.paused:
        raise HTTPException(status_code=400, detail="Only paused campaigns can be resumed.")
    campaign.status = CampaignStatus.active
    _set_orch_states_for_campaign(db, campaign, "active", "campaign_resumed")
    db.commit()
    return {"status": "active"}


@router.post("/{campaign_id}/resume-launch")
def resume_launch(campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Resume a partially failed launch. Enrolls only prospects whose sequences
    are still pending (skips those already enrolled) into LinkedIn orchestration.
    """
    campaign = _get_campaign(campaign_id, db, current_user)

    pending_sequences = (
        db.query(EmailSequence)
        .filter(
            EmailSequence.campaign_id == campaign_id,
            EmailSequence.approved == True,
            EmailSequence.status == SequenceStatus.pending,
        )
        .options(joinedload(EmailSequence.prospect))
        .all()
    )

    if not pending_sequences:
        raise HTTPException(
            status_code=400,
            detail="No pending sequences to enroll. All approved sequences are already enrolled.",
        )

    from .unsubscribes import is_suppressed
    from ..services.suppression import is_domain_excluded

    leads_enrolled = 0
    for seq in pending_sequences:
        if not seq.prospect:
            continue
        if is_suppressed(seq.prospect.email, db) or is_domain_excluded(seq.prospect.email, db):
            continue
        seq.status = SequenceStatus.enrolled
        if seq.prospect:
            seq.prospect.status = ProspectStatus.enrolled
        leads_enrolled += 1

    if campaign.status != CampaignStatus.active:
        campaign.status = CampaignStatus.active
    db.commit()

    # Resolve sequence config for correct LinkedIn day offsets.
    _resume_seq_config = None
    if getattr(campaign, "sequence_config_id", None):
        from ..db.models import SequenceConfigORM
        _sc_row = db.query(SequenceConfigORM).filter_by(id=campaign.sequence_config_id).first()
        if _sc_row and _sc_row.config_json:
            try:
                from ..orchestration.models import SequenceConfig
                _resume_seq_config = SequenceConfig(
                    **(_sc_row.config_json if isinstance(_sc_row.config_json, dict) else json.loads(_sc_row.config_json))
                )
            except Exception:
                pass
    try:
        _enroll_prospects_into_linkedin_orch(
            campaign=campaign,
            approved_sequences=pending_sequences,
            db=db,
            sequence_config=_resume_seq_config,
        )
        db.commit()
    except Exception as li_err:  # noqa: BLE001
        logger.error(
            "resume_launch: LinkedIn orchestration enrollment failed: %s",
            li_err, exc_info=True,
        )

    return {
        "campaign_id": campaign_id,
        "leads_enrolled": leads_enrolled,
        "status": campaign.status.value,
    }


@router.post("/{campaign_id}/archive")
def archive_campaign(campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    campaign = _get_campaign(campaign_id, db, current_user)
    campaign.status = CampaignStatus.archived
    db.commit()
    return {"status": "archived"}


@router.post("/{campaign_id}/clone")
def clone_campaign(campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    src = _get_campaign(campaign_id, db, current_user)

    clone = Campaign(
        id=str(uuid.uuid4()),
        user_id=current_user.id if current_user else src.user_id,
        name=f"Copy of {src.name}",
        goal=src.goal,
        value_prop=src.value_prop,
        segment_id=src.segment_id,
        prospect_ids=list(src.prospect_ids or []),
        sending_limit_per_day=src.sending_limit_per_day,
        ab_test_enabled=src.ab_test_enabled,
        status=CampaignStatus.draft,
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return _campaign_to_dict(clone)


@router.post("/mark-replied")
def mark_prospect_replied(
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually mark a prospect as replied (for replies the automated poller can't detect)."""
    from ..db.models import ProspectStatus
    seq_id = body.get("sequence_id")
    prospect_id = body.get("prospect_id")
    if not seq_id or not prospect_id:
        raise HTTPException(400, "sequence_id and prospect_id required")

    seq = db.query(EmailSequence).filter_by(id=seq_id).first()
    if not seq:
        raise HTTPException(404, "Sequence not found")

    # Verify ownership
    campaign = db.query(Campaign).filter_by(id=seq.campaign_id).first()
    if not campaign or campaign.user_id != current_user.id:
        raise HTTPException(404, "Not found")

    prospect = db.query(Prospect).filter_by(id=prospect_id).first()
    if not prospect:
        raise HTTPException(404, "Prospect not found")

    seq.status = SequenceStatus.replied
    seq.replied_at = datetime.now(timezone.utc)
    prospect.status = ProspectStatus.replied
    db.commit()

    return {"status": "replied", "prospect_id": prospect_id}


@router.delete("/{campaign_id}")
def delete_campaign(
    campaign_id: str,
    confirm: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently deletes a campaign and all its email sequences and send logs.

    Active or launched campaigns require confirm=true to prevent accidental deletion.
    """
    campaign = _get_campaign(campaign_id, db, current_user)

    # Guard: active/paused/launched campaigns need explicit confirmation
    if campaign.status in (CampaignStatus.active, CampaignStatus.paused) and not confirm:
        seq_count = db.query(EmailSequence).filter_by(campaign_id=campaign_id).count()
        raise HTTPException(
            status_code=409,
            detail="Campaign '{}' is {} with {} sequences. Pass confirm=true to delete permanently.".format(
                campaign.name, campaign.status.value, seq_count,
            ),
        )
    if campaign.launched_at and not confirm:
        raise HTTPException(
            status_code=409,
            detail="Campaign '{}' was launched. Pass confirm=true to delete permanently.".format(
                campaign.name,
            ),
        )

    # Stop orchestration states
    _set_orch_states_for_campaign(db, campaign, "stopped_cancelled", "campaign_deleted")

    # Reset prospect statuses BEFORE deleting sequences - prospects marked
    # 'enrolled' by this campaign should go back to 'pending' so they can
    # be used in other campaigns. Only reset if they have no sequences in
    # any OTHER active campaign.
    seq_prospect_ids = [
        row[0] for row in db.query(EmailSequence.prospect_id)
        .filter_by(campaign_id=campaign_id).all()
    ]
    if seq_prospect_ids:
        # Find which of these prospects are also in other campaigns
        in_other = {
            row[0] for row in db.query(EmailSequence.prospect_id)
            .filter(EmailSequence.campaign_id != campaign_id, EmailSequence.prospect_id.in_(seq_prospect_ids))
            .all()
        }
        reset_ids = [pid for pid in seq_prospect_ids if pid not in in_other]
        if reset_ids:
            db.query(Prospect).filter(
                Prospect.id.in_(reset_ids),
                Prospect.status == ProspectStatus.enrolled,
            ).update({"status": ProspectStatus.pending}, synchronize_session=False)

    db.query(SendLog).filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    db.query(EmailSequence).filter_by(campaign_id=campaign_id).delete(synchronize_session=False)
    # Clean up per-campaign AppSetting rows (instructions, booking link, project search)
    for prefix in ("campaign_step_instructions_", "campaign_booking_link_", "campaign_project_search_"):
        db.query(AppSetting).filter(AppSetting.key == f"{prefix}{campaign_id}").delete(synchronize_session=False)
    db.delete(campaign)
    db.commit()
    logger.info(f"Campaign {campaign_id} ({campaign.name!r}) deleted.")
    return {"deleted": campaign_id}


def _resolve_prospects(campaign: Campaign, db: Session) -> tuple[list[Prospect], bool, int, dict]:
    """Resolve which prospects are eligible for sequence generation in a campaign.

    Returns (prospects, capped, total_found, skip_summary).

    This is the audience resolution logic used by the AI generation background job.
    It determines WHO gets a generated message sequence by applying these filters:

    Resolution order for the audience:
      1. campaign.prospect_ids → explicit list of prospect IDs
      2. campaign.segment_id → all prospects in that ICP segment
      3. Neither set → returns empty (campaign has no audience)

    Exclusions (prospects skipped even if in the audience):
      - Already have a sequence in THIS campaign (prevents duplicate generation)
      - Already enrolled in ANOTHER active campaign (cross-campaign dedup)
      - Status is terminal (enrolled/connected/messaged/replied/bounced)
      - Email validation status is invalid (spamtrap, abuse, do_not_mail)

    Capping: at most PROSPECT_CAP (500) prospects are returned per run.
    If more are eligible, the caller must run additional batches.
    """
    # Guard: require an explicit audience selection
    if not campaign.prospect_ids and not campaign.segment_id:
        return [], False, 0, {
            "total_assigned": 0,
            "already_generated": 0,
            "invalid_email": 0,
            "bad_status": 0,
            "skipped_ids": [],
        }

    already_in_campaign_ids = {
        row[0] for row in db.query(EmailSequence.prospect_id).filter(
            EmailSequence.campaign_id == campaign.id
        ).all()
    }

    # Cross-campaign duplicate detection: find prospects enrolled in other ACTIVE campaigns
    active_campaign_statuses = [CampaignStatus.active, CampaignStatus.paused]
    active_other_enrollments = (
        db.query(EmailSequence.prospect_id, Campaign.id, Campaign.name)
        .join(Campaign, Campaign.id == EmailSequence.campaign_id)
        .filter(Campaign.id != campaign.id)
        .filter(Campaign.status.in_(active_campaign_statuses))
        .all()
    )
    # Map prospect_id -> list of (campaign_id, campaign_name) they are active in
    active_in_other: dict[str, list[tuple[str, str]]] = {}
    for pid, cid, cname in active_other_enrollments:
        active_in_other.setdefault(pid, []).append((cid, cname))

    # Count all assigned prospects for the skip summary
    all_q = db.query(Prospect)
    if campaign.prospect_ids:
        all_q = all_q.filter(Prospect.id.in_(campaign.prospect_ids))
    elif campaign.segment_id:
        all_q = all_q.filter(Prospect.segment_id == campaign.segment_id)
    all_prospects = all_q.all()
    total_assigned = len(all_prospects)

    # Build skip summary with prospect IDs for debugging
    eligible_statuses = [ProspectStatus.new, ProspectStatus.pending]
    skip_already = 0
    skip_invalid = 0
    skip_status = 0
    skip_active_other = 0
    skipped_ids: list[str] = []

    for p in all_prospects:
        if p.id in already_in_campaign_ids:
            skip_already += 1
            skipped_ids.append(p.id)
        elif p.id in active_in_other:
            skip_active_other += 1
            skipped_ids.append(p.id)
            other_campaigns = active_in_other[p.id]
            campaign_names = ", ".join(f"{cname} ({cid[:8]})" for cid, cname in other_campaigns)
            logger.info(
                "Skipping prospect %s (%s %s)  -  already active in: %s",
                p.id, p.first_name, p.last_name or "", campaign_names,
            )
        elif p.status not in eligible_statuses:
            skip_status += 1
            skipped_ids.append(p.id)
        elif p.email_validation_status and p.email_validation_status in INVALID_EMAIL_STATUSES:
            skip_invalid += 1
            skipped_ids.append(p.id)

    skip_summary = {
        "total_assigned": total_assigned,
        "already_generated": skip_already,
        "invalid_email": skip_invalid,
        "bad_status": skip_status,
        "active_in_other_campaign": skip_active_other,
        "skipped_ids": skipped_ids,
        "_active_other_map": active_in_other,  # internal: prospect_id -> [(camp_id, camp_name)]
    }

    already_in_campaign = db.query(EmailSequence.prospect_id).filter(
        EmailSequence.campaign_id == campaign.id
    ).scalar_subquery()

    # Collect IDs of prospects active in other campaigns for exclusion
    active_other_ids = set(active_in_other.keys())

    q = (
        db.query(Prospect)
        .filter(Prospect.status.in_(eligible_statuses))
        .filter(Prospect.id.notin_(already_in_campaign))
        .filter(
            Prospect.email_validation_status.is_(None)
            | ~Prospect.email_validation_status.in_(list(INVALID_EMAIL_STATUSES))
        )
    )

    if campaign.prospect_ids:
        # Filter out prospects active in other campaigns from the explicit list
        filtered_ids = [pid for pid in campaign.prospect_ids if pid not in active_other_ids]
        q = q.filter(Prospect.id.in_(filtered_ids))
    elif campaign.segment_id:
        q = q.filter(Prospect.segment_id == campaign.segment_id)
        if active_other_ids:
            q = q.filter(Prospect.id.notin_(list(active_other_ids)))

    total = q.count()
    prospects = q.limit(PROSPECT_CAP).all()
    capped = total > PROSPECT_CAP
    if capped:
        logger.warning(
            f"Campaign {campaign.id}: {total} eligible prospects found, capped at {PROSPECT_CAP}. "
            "Run multiple campaigns or raise PROSPECT_CAP to process the rest."
        )
    return prospects, capped, total, skip_summary


# ── Serialisation helpers ────────────────────────────────────────────────────

def _campaign_to_dict(c: Campaign) -> dict:
    """Serialise a Campaign ORM object to a JSON-safe dict for API responses."""
    return {
        "id": c.id,
        "user_id": c.user_id,
        "user_name": c.user.name if c.user_id and hasattr(c, "user") and c.user else None,
        "name": c.name,
        "goal": c.goal,
        "value_prop": c.value_prop,
        "segment_id": c.segment_id,
        "prospect_ids": c.prospect_ids or [],
        "status": c.status.value if c.status else "draft",
        "ab_test_enabled": bool(c.ab_test_enabled),
        "tokens_input": c.tokens_input or 0,
        "tokens_output": c.tokens_output or 0,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "launched_at": c.launched_at.isoformat() if c.launched_at else None,
        "sequence_config_id": getattr(c, "sequence_config_id", None),
        "sequence_config_name": (
            c.sequence_config.name
            if getattr(c, "sequence_config_id", None) and getattr(c, "sequence_config", None)
            else None
        ),
        # Multi-account LinkedIn: expose which accounts are assigned to this campaign.
        "outreach_account_id": getattr(c, "outreach_account_id", None),
        "outreach_account_name": (
            c.outreach_account.name
            if getattr(c, "outreach_account_id", None) and getattr(c, "outreach_account", None)
            else None
        ),
        "scrape_account_id": getattr(c, "scrape_account_id", None),
        "scrape_account_name": (
            c.scrape_account.name
            if getattr(c, "scrape_account_id", None) and getattr(c, "scrape_account", None)
            else None
        ),
    }


def _sequence_to_dict(seq: EmailSequence) -> dict:
    """Serialise an EmailSequence ORM object to a JSON-safe dict for API responses."""
    p = seq.prospect
    return {
        "id": seq.id,
        "campaign_id": seq.campaign_id,
        "prospect_id": seq.prospect_id,
        "prospect_name": f"{p.first_name} {p.last_name or ''}".strip() if p else "",
        "prospect_email": p.email if p else "",
        "prospect_company": p.company if p else "",
        "linkedin_connect_note": seq.linkedin_connect_note,
        "linkedin_message": seq.linkedin_message,
        "approved": seq.approved,
        "status": seq.status.value if seq.status else "pending",
        "created_at": seq.created_at.isoformat() if seq.created_at else None,
    }


# ── AI-powered LinkedIn message generation ────────────────────────────────────
# These endpoints use Claude (MODEL_SMART) to generate personalised LinkedIn
# connection notes and follow-up messages based on prospect data, campaign goals,
# the user's writing style profile, and sales copy principles.
#
# The flow is:
#   1. generate-sample — generates messages for the FIRST prospect as a sample
#   2. Operator reviews and optionally uses refine-message to iterate
#   3. apply-to-all — uses the approved sample as a reference to generate
#      personalised messages for ALL other prospects in the campaign
#
# Each Claude call costs Anthropic API credits (tracked in Campaign.tokens_input/output).


class GenerateSampleRequest(BaseModel):
    context: Optional[str] = None  # optional extra context from user


class RefineRequest(BaseModel):
    step: str  # "linkedin_connect_note" or "linkedin_message"
    current_text: str
    feedback: str
    sequence_id: str


class ApplyToAllRequest(BaseModel):
    reference_sequence_id: str


def _load_sales_copy_principles() -> str:
    """Load sales_copy_principles.md from the project root. This markdown file
    contains the sales copy guardrails (no buzzwords, no filler, specific
    formatting rules) that are injected into every AI generation prompt."""
    principles_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "sales_copy_principles.md",
    )
    try:
        with open(principles_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("sales_copy_principles.md not found at %s", principles_path)
        return ""


def _load_style_block(db: Session, user_id: Optional[str]) -> str:
    """Load the user's writing style profile for injection into AI prompts.

    Combines two sources:
      1. StyleProfile (from Outlook email analysis) — formality, sentence length, etc.
      2. writing_style markdown setting (manually editable in dashboard Settings)
    Per-user settings take priority over global defaults."""
    from ..db.models import StyleProfile
    from ..services.style_analyzer import build_style_prompt

    db_style = ""
    try:
        q = db.query(StyleProfile)
        if user_id is not None:
            q = q.filter(StyleProfile.user_id == user_id)
        else:
            q = q.filter(StyleProfile.user_id.is_(None))
        profile = q.order_by(StyleProfile.synced_at.desc()).first()
        if profile and user_id is not None and profile.user_id != user_id:
            profile = None
        db_style = build_style_prompt(profile.raw_profile) if profile and profile.raw_profile else ""
    except Exception as exc:
        logger.warning("Failed to load StyleProfile for LinkedIn gen: %s", exc)

    # Also load the writing_style markdown setting
    writing_style_md = ""
    if user_id:
        user_row = db.query(AppSetting).filter_by(
            key=f"__user__{user_id}__writing_style"
        ).first()
        if user_row and user_row.value:
            writing_style_md = user_row.value
    if not writing_style_md:
        global_row = db.query(AppSetting).filter_by(key="writing_style").first()
        writing_style_md = global_row.value if global_row and global_row.value else ""

    parts = [p for p in [writing_style_md, db_style] if p]
    return "\n\n".join(parts)


@router.post("/{campaign_id}/generate-sample")
def generate_sample(
    campaign_id: str,
    req: GenerateSampleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate AI-powered LinkedIn messages for the first prospect in a campaign."""
    from .conference._shared import get_anthropic_client
    from ..config import MODEL_SMART
    from ..services.email_gen_helpers import _sanitize

    campaign = _get_campaign(campaign_id, db, current_user)

    # Resolve prospect IDs — try campaign.prospect_ids first, then fall back
    # to EmailSequence rows (created by /prepare from a segment)
    prospect_ids = (
        json.loads(campaign.prospect_ids)
        if isinstance(campaign.prospect_ids, str)
        else (campaign.prospect_ids or [])
    )
    if not prospect_ids:
        # Fall back: get prospect IDs from existing sequences
        prospect_ids = [
            pid for (pid,) in
            db.query(EmailSequence.prospect_id)
            .filter(EmailSequence.campaign_id == campaign_id)
            .limit(1)
            .all()
        ]
    if not prospect_ids:
        raise HTTPException(422, "No prospects in campaign. Prepare the campaign first.")

    # Pick the FIRST prospect
    prospect = db.query(Prospect).filter(Prospect.id == prospect_ids[0]).first()
    if not prospect:
        raise HTTPException(404, "First prospect not found")

    # Load style profile and sales copy principles
    style_block = _load_style_block(db, current_user.id if current_user else None)
    sales_principles = _load_sales_copy_principles()

    # Sanitize prospect fields
    first_name = _sanitize(prospect.first_name or "", 100)
    last_name = _sanitize(prospect.last_name or "", 100)
    title = _sanitize(prospect.title or "", 200)
    company = _sanitize(prospect.company or "", 200)
    extra_context = _sanitize(req.context or "", 500) if req.context else ""

    # Build the generation prompt
    context_section = f"\nAdditional context: {extra_context}" if extra_context else ""
    prompt = f"""You are writing LinkedIn outreach messages for a sales campaign.

TARGET PROSPECT:
- Name: {first_name} {last_name}
- Title: {title}
- Company: {company}
{context_section}

CAMPAIGN:
- Goal: {_sanitize(campaign.goal or '', 500)}
- Value proposition: {_sanitize(campaign.value_prop or '', 500)}

{style_block}

SALES COPY PRINCIPLES:
{sales_principles}

TASK: Generate two LinkedIn messages for this prospect.

1. linkedin_connect_note (connection request note):
   - Max 300 characters, max 30 words
   - One plain sentence about what we do
   - Reference their role or company specifically
   - No buzzwords, no "I'd love to connect" -- be direct
   - End with a simple question or reason to connect

2. linkedin_message (follow-up after connection accepted):
   - Max 500 characters
   - Thank them briefly for connecting
   - Say what we do with a measurable result
   - Reference their role/company/industry
   - One clear CTA -- a simple question
   - No filler, no marketing language

Return valid JSON only:
{{"linkedin_connect_note": "...", "linkedin_message": "..."}}"""

    try:
        client = get_anthropic_client()
        msg = client.messages.create(
            model=MODEL_SMART,
            max_tokens=500,
            timeout=60,
            messages=[{"role": "user", "content": prompt}],
        )
        if not msg.content:
            raise HTTPException(502, "AI returned empty response")
        raw = msg.content[0].text.strip()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("generate-sample: Claude call failed: %s", exc, exc_info=True)
        raise HTTPException(502, f"AI generation failed: {exc}")

    # Parse the JSON response
    from ..services.email_gen_helpers import _parse_json_response
    try:
        parsed = _parse_json_response(raw)
    except ValueError:
        logger.error("generate-sample: failed to parse AI response: %s", raw[:500])
        raise HTTPException(502, "AI returned unparseable response")

    li_connect_note = parsed.get("linkedin_connect_note", "")
    li_message = parsed.get("linkedin_message", "")

    # Create or update EmailSequence for this prospect
    existing = db.query(EmailSequence).filter_by(
        campaign_id=campaign_id, prospect_id=prospect.id
    ).first()
    if existing:
        existing.linkedin_connect_note = li_connect_note
        existing.linkedin_message = li_message
        existing.approved = False
        seq = existing
    else:
        seq = EmailSequence(
            id=str(uuid.uuid4()),
            campaign_id=campaign_id,
            prospect_id=prospect.id,
            linkedin_connect_note=li_connect_note,
            linkedin_message=li_message,
            approved=False,
            status=SequenceStatus.pending,
        )
        db.add(seq)
    db.commit()
    db.refresh(seq)

    return {
        "sequence_id": seq.id,
        "prospect": {
            "name": f"{prospect.first_name} {prospect.last_name or ''}".strip(),
            "title": prospect.title or "",
            "company": prospect.company or "",
        },
        "linkedin_connect_note": li_connect_note,
        "linkedin_message": li_message,
    }


@router.patch("/{campaign_id}/refine-message")
def refine_message(
    campaign_id: str,
    req: RefineRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Refine a single LinkedIn message field using AI based on user feedback."""
    from .conference._shared import get_anthropic_client
    from ..config import MODEL_SMART
    from ..services.email_gen_helpers import _sanitize

    _get_campaign(campaign_id, db, current_user)  # ownership check

    # Load the sequence
    seq = db.query(EmailSequence).filter_by(
        id=req.sequence_id, campaign_id=campaign_id
    ).first()
    if not seq:
        raise HTTPException(404, "Sequence not found")

    # Load the prospect for context
    prospect = db.query(Prospect).filter_by(id=seq.prospect_id).first()
    if not prospect:
        raise HTTPException(404, "Prospect not found")

    # Validate step
    if req.step not in ("linkedin_connect_note", "linkedin_message"):
        raise HTTPException(400, f"Unknown step: {req.step}. Must be 'linkedin_connect_note' or 'linkedin_message'.")

    first_name = _sanitize(prospect.first_name or "", 100)
    last_name = _sanitize(prospect.last_name or "", 100)
    title = _sanitize(prospect.title or "", 200)
    company = _sanitize(prospect.company or "", 200)
    current_text = req.current_text.strip()
    feedback = req.feedback.strip()

    if req.step == "linkedin_connect_note":
        label = "LinkedIn connection request note"
        constraint = "Return ONLY the revised text, max 300 characters, max 30 words."
    else:
        label = "LinkedIn message"
        constraint = "Return ONLY the revised text, max 500 characters."

    prompt = f"""You wrote this {label} for {first_name} {last_name}, {title} at {company}:

{current_text}

The user wants you to revise it with this feedback: "{feedback}"

Apply the feedback. {constraint} No JSON, no quotes, no explanation."""

    try:
        client = get_anthropic_client()
        msg = client.messages.create(
            model=MODEL_SMART,
            max_tokens=400,
            timeout=60,
            messages=[{"role": "user", "content": prompt}],
        )
        if not msg.content:
            raise HTTPException(502, "AI returned empty response")
        revised = msg.content[0].text.strip()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("refine-message: Claude call failed: %s", exc, exc_info=True)
        raise HTTPException(502, f"AI refinement failed: {exc}")

    # Update the sequence field
    if req.step == "linkedin_connect_note":
        seq.linkedin_connect_note = revised
    else:
        seq.linkedin_message = revised
    seq.approved = False
    db.commit()
    db.refresh(seq)

    return {
        "step": req.step,
        "text": revised,
        "sequence_id": seq.id,
    }


@router.post("/{campaign_id}/apply-to-all")
def apply_to_all(
    campaign_id: str,
    req: ApplyToAllRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Apply the approved sample's style to all other prospects in the campaign."""
    from .conference._shared import get_anthropic_client
    from ..config import MODEL_SMART
    from ..services.email_gen_helpers import _sanitize

    campaign = _get_campaign(campaign_id, db, current_user)

    # Load the reference sequence
    ref_seq = db.query(EmailSequence).filter_by(
        id=req.reference_sequence_id, campaign_id=campaign_id
    ).first()
    if not ref_seq:
        raise HTTPException(404, "Reference sequence not found")

    ref_connect_note = ref_seq.linkedin_connect_note or ""
    ref_message = ref_seq.linkedin_message or ""

    if not ref_connect_note.strip() and not ref_message.strip():
        raise HTTPException(422, "Reference sequence has no LinkedIn content to apply")

    # Respect skip_connect_note from the campaign's sequence config
    skip_connect_note = False
    scid = getattr(campaign, "sequence_config_id", None)
    if scid:
        from ..db.models import SequenceConfigORM
        sc_row = db.query(SequenceConfigORM).filter_by(id=scid).first()
        if sc_row and sc_row.config_json:
            raw_cfg = sc_row.config_json if isinstance(sc_row.config_json, dict) else json.loads(sc_row.config_json)
            for step in raw_cfg.get("steps", []):
                if step.get("step_type") == "send_linkedin_connect" and step.get("skip_connect_note"):
                    skip_connect_note = True
                    break
    if skip_connect_note:
        ref_connect_note = ""

    # Resolve all prospect IDs — campaign.prospect_ids or fall back to sequences
    prospect_ids = (
        json.loads(campaign.prospect_ids)
        if isinstance(campaign.prospect_ids, str)
        else (campaign.prospect_ids or [])
    )
    if not prospect_ids:
        prospect_ids = [
            pid for (pid,) in
            db.query(EmailSequence.prospect_id)
            .filter(EmailSequence.campaign_id == campaign_id)
            .all()
        ]
    if not prospect_ids:
        raise HTTPException(422, "No prospects in campaign")

    # Exclude the reference prospect
    other_prospect_ids = [pid for pid in prospect_ids if pid != ref_seq.prospect_id]
    if not other_prospect_ids:
        return {"prepared": 0, "total": 0}

    # Load all other prospects
    prospects = db.query(Prospect).filter(Prospect.id.in_(other_prospect_ids)).all()
    prospect_map = {p.id: p for p in prospects}

    client = get_anthropic_client()
    prepared = 0
    errors = 0

    for pid in other_prospect_ids:
        prospect = prospect_map.get(pid)
        if not prospect:
            continue

        first_name = _sanitize(prospect.first_name or "", 100)
        last_name = _sanitize(prospect.last_name or "", 100)
        title = _sanitize(prospect.title or "", 200)
        company = _sanitize(prospect.company or "", 200)

        generated_note = ""
        generated_msg = ""

        # Generate personalized connect note
        if ref_connect_note.strip():
            note_prompt = f"""Here is a reference LinkedIn connection request note that was approved:

"{ref_connect_note}"

Write a similar one for:
- Name: {first_name} {last_name}
- Title: {title}
- Company: {company}

Match the tone, length, and structure exactly. Only change the personalization to fit this person's role and company. Max 300 characters, max 30 words. Return ONLY the text."""

            try:
                msg = client.messages.create(
                    model=MODEL_SMART,
                    max_tokens=300,
                    timeout=60,
                    messages=[{"role": "user", "content": note_prompt}],
                )
                if msg.content:
                    generated_note = msg.content[0].text.strip()
            except Exception as exc:
                logger.warning("apply-to-all: connect note failed for %s: %s", pid, exc)
                errors += 1

        # Generate personalized message
        if ref_message.strip():
            msg_prompt = f"""Here is a reference LinkedIn follow-up message that was approved:

"{ref_message}"

Write a similar one for:
- Name: {first_name} {last_name}
- Title: {title}
- Company: {company}

Match the tone, length, and structure exactly. Only change the personalization to fit this person's role and company. Max 500 characters. Return ONLY the text."""

            try:
                msg = client.messages.create(
                    model=MODEL_SMART,
                    max_tokens=400,
                    timeout=60,
                    messages=[{"role": "user", "content": msg_prompt}],
                )
                if msg.content:
                    generated_msg = msg.content[0].text.strip()
            except Exception as exc:
                logger.warning("apply-to-all: message failed for %s: %s", pid, exc)
                errors += 1

        # Create or update EmailSequence
        existing = db.query(EmailSequence).filter_by(
            campaign_id=campaign_id, prospect_id=pid
        ).first()
        if existing:
            if generated_note:
                existing.linkedin_connect_note = generated_note
            if generated_msg:
                existing.linkedin_message = generated_msg
            existing.approved = True
        else:
            seq = EmailSequence(
                id=str(uuid.uuid4()),
                campaign_id=campaign_id,
                prospect_id=pid,
                linkedin_connect_note=generated_note or None,
                linkedin_message=generated_msg or None,
                approved=True,
                status=SequenceStatus.pending,
            )
            db.add(seq)
        prepared += 1

    db.commit()

    result = {"prepared": prepared, "total": len(other_prospect_ids)}
    if errors:
        result["errors"] = errors
    return result
