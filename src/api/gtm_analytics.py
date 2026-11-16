"""
GTM Analytics API — unified metrics dashboard across all outreach channels.

This module combines automated metrics (from conference leads, cold email campaigns,
LinkedIn outreach) with manually entered data (referrals, inbound leads, etc.) into
a single dashboard view. It also includes the follow-ups machine endpoints for
HubSpot deal monitoring.

The dashboard has three major sections:

  1. **GTM Summary**: Aggregated monthly metrics across channels (linkedin, cold_email,
     inbound, conference, referral). Automated data comes from ConferenceLead and
     campaign tables; manual entries (GtmManualEntry) fill gaps where automation
     doesn't reach (e.g., referral meetings, inbound inquiries from trade shows).
     The build_dashboard() aggregator lives in services/gtm_aggregator.py.

  2. **Manual Entries CRUD**: Create/update/delete metric entries for things the
     system can't track automatically. Supports upsert (same user+category+metric+
     month+source_ref → update). Non-admins can only create entries for themselves.
     Valid categories: linkedin, cold_email, inbound, conference, referral.

  3. **Follow-ups Machine**: Manages HubSpot deal monitoring — list overdue deals,
     snooze/unsnooze, manual scan trigger, config for active stages and Trello
     integration. Also provides endpoints for connecting the sales@ inbound inbox
     via Outlook device code flow.

Endpoints:
  GET  /gtm-analytics/summary           — aggregated monthly metrics with filters
  GET  /gtm-analytics/conferences       — distinct conference names for filter dropdown
  GET  /gtm-analytics/entries           — list manual entries (filterable)
  POST /gtm-analytics/entries           — create/upsert a manual entry
  PUT  /gtm-analytics/entries/{id}      — update a manual entry
  DELETE /gtm-analytics/entries/{id}    — delete a manual entry
  GET  /gtm-analytics/followups         — list deals with overdue/search/stage filters
  POST /gtm-analytics/followups/scan    — trigger immediate HubSpot sync
  POST /gtm-analytics/followups/{id}/snooze — snooze a deal for N days
  GET  /gtm-analytics/followups/config  — read follow-ups config (stages, Trello)
  PUT  /gtm-analytics/followups/config  — update follow-ups config
  GET  /gtm-analytics/inbound-inbox/*   — sales@ inbox connection management
"""
from __future__ import annotations

import uuid
import logging
from typing import Optional
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import ConferenceLead, GtmManualEntry, FollowupDeal, AppSetting, User
from ..services.gtm_aggregator import build_dashboard
from .auth import get_current_user

router = APIRouter(prefix="/gtm-analytics", tags=["gtm-analytics"])
logger = logging.getLogger(__name__)


# ── Pydantic models ──────────────────────────────────────────────────────

class ManualEntryCreate(BaseModel):
    user_id: Optional[str] = None
    category: str                   # linkedin | cold_email | inbound | conference | referral
    metric_key: str                 # meetings_booked | referral_meetings | inbound_lead | etc.
    month: str                      # YYYY-MM
    value: int = 1
    note: Optional[str] = None
    source_ref: Optional[str] = None


class ManualEntryUpdate(BaseModel):
    value: Optional[int] = None
    note: Optional[str] = None
    source_ref: Optional[str] = None


# ── Summary ──────────────────────────────────────────────────────────────

@router.get("/summary")
def get_gtm_summary(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    user_id: Optional[str] = None,
    category: Optional[str] = Query(None, description="CSV: linkedin,cold_email,..."),
    conference_name: Optional[str] = Query(None, description="CSV conference names"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    today = date.today()
    start = date(today.year, 1, 1)
    end = today

    if start_date:
        try:
            start = date.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(400, f"Invalid start_date: {start_date}")
    if end_date:
        try:
            end = date.fromisoformat(end_date)
        except ValueError:
            raise HTTPException(400, f"Invalid end_date: {end_date}")

    categories = [c.strip() for c in category.split(",")] if category else None
    conf_names = [c.strip() for c in conference_name.split(",")] if conference_name else None

    return build_dashboard(db, start, end, user_id, categories, conf_names)


# ── Conference list ──────────────────────────────────────────────────────

@router.get("/conferences")
def list_conferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.query(ConferenceLead.conference_name).distinct().order_by(
        ConferenceLead.conference_name).all()
    return [r[0] for r in rows]


# ── Manual entries CRUD ──────────────────────────────────────────────────

# Valid channel categories for manual GTM entries. Each maps to a section
# in the GTM dashboard. "conference" entries are auto-populated from
# ConferenceLead data; the others are manual unless automation is added.
VALID_CATEGORIES = {"linkedin", "cold_email", "inbound", "conference", "referral"}


@router.get("/entries")
def list_entries(
    category: Optional[str] = None,
    month: Optional[str] = None,
    user_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(GtmManualEntry).order_by(GtmManualEntry.month.desc(), GtmManualEntry.created_at.desc())
    if category:
        q = q.filter(GtmManualEntry.category == category)
    if month:
        q = q.filter(GtmManualEntry.month == month)
    if user_id:
        q = q.filter(GtmManualEntry.user_id == user_id)
    entries = q.limit(500).all()
    return [_entry_to_dict(e) for e in entries]


@router.post("/entries")
def create_entry(
    body: ManualEntryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(400, f"Invalid category: {body.category}. Must be one of {VALID_CATEGORIES}")

    # SWAT-M7: enforce user_id scope — non-admins can only create entries for themselves
    from ..db.models import UserRole
    if current_user.role != UserRole.admin and body.user_id != current_user.id:
        raise HTTPException(403, "You can only create entries for your own user ID.")

    # Validate month format
    if len(body.month) != 7 or body.month[4] != "-":
        raise HTTPException(400, f"Invalid month format: {body.month}. Use YYYY-MM")

    # Upsert: check if entry already exists for same user+category+metric+month+source_ref
    existing = db.query(GtmManualEntry).filter(
        GtmManualEntry.user_id == body.user_id,
        GtmManualEntry.category == body.category,
        GtmManualEntry.metric_key == body.metric_key,
        GtmManualEntry.month == body.month,
        GtmManualEntry.source_ref == body.source_ref,
    ).first()

    now = datetime.now(timezone.utc)

    if existing:
        existing.value = body.value
        existing.note = body.note
        existing.updated_at = now
        db.commit()
        db.refresh(existing)
        return _entry_to_dict(existing)

    entry = GtmManualEntry(
        id=str(uuid.uuid4()),
        user_id=body.user_id,
        category=body.category,
        metric_key=body.metric_key,
        month=body.month,
        value=body.value,
        note=body.note,
        source_ref=body.source_ref,
        created_by=current_user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _entry_to_dict(entry)


@router.put("/entries/{entry_id}")
def update_entry(
    entry_id: str,
    body: ManualEntryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entry = db.query(GtmManualEntry).filter(GtmManualEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(404, "Entry not found")

    if body.value is not None:
        entry.value = body.value
    if body.note is not None:
        entry.note = body.note
    if body.source_ref is not None:
        entry.source_ref = body.source_ref
    entry.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(entry)
    return _entry_to_dict(entry)


@router.delete("/entries/{entry_id}")
def delete_entry(
    entry_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entry = db.query(GtmManualEntry).filter(GtmManualEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(404, "Entry not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}


# ── Inbound inbox connection ──────────────────────────────────────────────

INBOUND_INBOX_ID = "inbound-sales"

@router.get("/inbound-inbox/status")
def inbound_inbox_status(
    current_user: User = Depends(get_current_user),
):
    """Check whether the sales@ inbox is connected for inbound lead detection."""
    from ..services.inbound_lead_poller import POLLER_INBOX
    from .outlook_auth import _check_auth
    info = _check_auth(INBOUND_INBOX_ID)
    return {
        "connected": bool(info and info.get("authenticated")),
        "account": info.get("account") if info else None,
        "target_inbox": POLLER_INBOX,
    }


@router.post("/inbound-inbox/connect")
def inbound_inbox_connect(
    current_user: User = Depends(get_current_user),
):
    """Initiate Outlook device code flow for the sales@ shared mailbox."""
    from .outlook_auth import _initiate
    return _initiate(INBOUND_INBOX_ID)


@router.post("/inbound-inbox/complete")
def inbound_inbox_complete(
    current_user: User = Depends(get_current_user),
):
    """Start polling for device code completion."""
    from .outlook_auth import _complete
    return _complete(INBOUND_INBOX_ID)


@router.get("/inbound-inbox/auth-status")
def inbound_inbox_auth_poll(
    current_user: User = Depends(get_current_user),
):
    """Poll for auth completion status (used by frontend polling)."""
    from .outlook_auth import _status
    return _status(INBOUND_INBOX_ID)


@router.post("/inbound-inbox/disconnect")
def inbound_inbox_disconnect(
    current_user: User = Depends(get_current_user),
):
    """Disconnect the inbound sales inbox."""
    from .outlook_auth import _disconnect
    return _disconnect(INBOUND_INBOX_ID)


# ── Helpers ──────────────────────────────────────────────────────────────

def _entry_to_dict(e: GtmManualEntry) -> dict:
    return {
        "id": e.id,
        "user_id": e.user_id,
        "category": e.category,
        "metric_key": e.metric_key,
        "month": e.month,
        "value": e.value,
        "note": e.note,
        "source_ref": e.source_ref,
        "created_by": e.created_by,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════
# FOLLOW-UPS MACHINE
# ══════════════════════════════════════════════════════════════════════════

def _deal_to_dict(d: FollowupDeal) -> dict:
    return {
        "id": d.id,
        "hubspot_deal_id": d.hubspot_deal_id,
        "deal_name": d.deal_name,
        "deal_stage": d.deal_stage,
        "amount": d.amount,
        "owner_name": d.owner_name,
        "owner_email": d.owner_email,
        "company_name": d.company_name,
        "contact_name": d.contact_name,
        "contact_email": d.contact_email,
        "last_activity_at": d.last_activity_at.isoformat() if d.last_activity_at else None,
        "last_synced_at": d.last_synced_at.isoformat() if d.last_synced_at else None,
        "days_since_activity": d.days_since_activity,
        "is_overdue": d.is_overdue,
        "trello_card_id": d.trello_card_id,
        "trello_card_url": d.trello_card_url,
        "snoozed_until": d.snoozed_until.isoformat() if d.snoozed_until else None,
        "suggested_next_step": d.suggested_next_step,
    }


@router.get("/followups")
def list_followups(
    search: Optional[str] = None,
    overdue_only: bool = False,
    stage: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(FollowupDeal).order_by(FollowupDeal.days_since_activity.desc())
    if overdue_only:
        q = q.filter(FollowupDeal.is_overdue == True)
    if stage:
        q = q.filter(FollowupDeal.deal_stage == stage)
    if search:
        term = f"%{search}%"
        q = q.filter(
            (FollowupDeal.deal_name.ilike(term)) |
            (FollowupDeal.company_name.ilike(term)) |
            (FollowupDeal.owner_name.ilike(term)) |
            (FollowupDeal.contact_name.ilike(term))
        )
    deals = q.limit(500).all()
    return [_deal_to_dict(d) for d in deals]


@router.post("/followups/scan")
def scan_followups(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from ..services.followups_poller import poll_deals
    result = poll_deals(db)
    return result


@router.post("/followups/{deal_id}/snooze")
def snooze_deal(
    deal_id: str,
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deal = db.query(FollowupDeal).filter(FollowupDeal.id == deal_id).first()
    if not deal:
        raise HTTPException(404, "Deal not found")
    deal.snoozed_until = datetime.now(timezone.utc) + timedelta(days=days)
    deal.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "snoozed_until": deal.snoozed_until.isoformat()}


@router.post("/followups/{deal_id}/unsnooze")
def unsnooze_deal(
    deal_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deal = db.query(FollowupDeal).filter(FollowupDeal.id == deal_id).first()
    if not deal:
        raise HTTPException(404, "Deal not found")
    deal.snoozed_until = None
    deal.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


# ── Follow-ups config ────────────────────────────────────────────────────

import json as _json
from datetime import timedelta

FOLLOWUP_CONFIG_KEYS = {
    "followups_active_stage_ids": list,
    "followups_trello_board_id": str,
    "followups_trello_list_id": str,
}


@router.get("/followups/config")
def get_followups_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = {}
    for key in FOLLOWUP_CONFIG_KEYS:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        val = row.value if row else None
        if FOLLOWUP_CONFIG_KEYS[key] == list and val:
            try:
                val = _json.loads(val)
            except (TypeError, _json.JSONDecodeError):
                val = []
        config[key] = val
    import os
    config["hubspot_configured"] = bool(os.getenv("HUBSPOT_API_TOKEN"))
    config["trello_configured"] = bool(os.getenv("TRELLO_API_KEY") and os.getenv("TRELLO_API_TOKEN"))
    # Owner name mapping
    owner_row = db.query(AppSetting).filter(AppSetting.key == "followups_owner_map").first()
    config["followups_owner_map"] = owner_row.value if owner_row else ""
    return config


class FollowupsConfigUpdate(BaseModel):
    active_stage_ids: Optional[list[str]] = None
    trello_board_id: Optional[str] = None
    trello_list_id: Optional[str] = None
    owner_map: Optional[str] = None


@router.put("/followups/config")
def update_followups_config(
    body: FollowupsConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    updates = {}
    if body.active_stage_ids is not None:
        updates["followups_active_stage_ids"] = _json.dumps(body.active_stage_ids)
    if body.trello_board_id is not None:
        updates["followups_trello_board_id"] = body.trello_board_id
    if body.trello_list_id is not None:
        updates["followups_trello_list_id"] = body.trello_list_id
    if body.owner_map is not None:
        updates["followups_owner_map"] = body.owner_map

    for key, val in updates.items():
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row:
            row.value = val
            row.updated_at = now
        else:
            db.add(AppSetting(key=key, value=val, updated_at=now))
    db.commit()
    return {"ok": True}


@router.get("/followups/hubspot-stages")
def get_hubspot_stages(
    current_user: User = Depends(get_current_user),
):
    from ..services.hubspot import HubSpotClient
    try:
        hs = HubSpotClient()
        try:
            return hs.list_pipelines()
        finally:
            hs.close()
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/followups/trello-boards")
def get_trello_boards(
    current_user: User = Depends(get_current_user),
):
    from ..services.trello import TrelloClient
    try:
        tr = TrelloClient()
        try:
            boards = tr.list_boards()
            for b in boards:
                b["lists"] = tr.list_board_lists(b["id"])
            return boards
        finally:
            tr.close()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
