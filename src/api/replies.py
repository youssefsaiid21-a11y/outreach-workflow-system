"""
Reply management API  -  inbox-style view for classified prospect replies.

Endpoints:
    GET  /replies/          List replies with filters (campaign, classification, status, dates)
    GET  /replies/stats     Counts by classification and status (for badges)
    GET  /replies/{id}      Single reply detail
    POST /replies/{id}/action  Take action: meeting_booked, snooze, archive
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..db.database import get_db
from ..db.models import Prospect, Reply, ReplyStatus, Campaign, User
from .auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/replies", tags=["replies"])


def _user_campaign_ids(db: Session, current_user: "User | None") -> Optional[list[str]]:
    """FIX-14: list of campaign ids the caller owns. None means no scoping
    needed (legacy first-run with zero users)."""
    if current_user is None:
        if db.query(User).count() == 0:
            return None
        raise HTTPException(401, "Authentication required.")
    return [r[0] for r in db.query(Campaign.id).filter(Campaign.user_id == current_user.id).all()]


# ── Request / Response models ────────────────────────────────────────────────

class ReplyActionRequest(BaseModel):
    action: str  # "meeting_booked" | "snooze" | "archive"
    snooze_until: Optional[str] = None  # ISO date for snooze


# ── Helpers ──────────────────────────────────────────────────────────────────

def _reply_to_dict(r: Reply) -> dict:
    prospect = r.prospect
    campaign = r.campaign
    return {
        "id": r.id,
        "prospect_id": r.prospect_id,
        "prospect_name": f"{prospect.first_name} {prospect.last_name or ''}".strip() if prospect else "",
        "prospect_email": prospect.email if prospect else r.from_email,
        "prospect_company": prospect.company if prospect else "",
        "prospect_title": prospect.title if prospect else "",
        "campaign_id": r.campaign_id,
        "campaign_name": campaign.name if campaign else "",
        "subject": r.subject,
        "body": r.body,
        "from_email": r.from_email,
        "classification": r.classification,
        "status": r.status.value if r.status else "new",
        "snoozed_until": r.snoozed_until.isoformat() if r.snoozed_until else None,
        "received_at": r.received_at.isoformat() if r.received_at else None,
        "classified_at": r.classified_at.isoformat() if r.classified_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/stats")
def get_reply_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Counts by classification and status  -  for sidebar badges and filter bar.

    FIX-14: scoped to the current user's campaigns.
    """
    user_cids = _user_campaign_ids(db, current_user)
    base_filter = []
    if user_cids is not None:
        base_filter.append(Reply.campaign_id.in_(user_cids))

    by_classification = (
        db.query(Reply.classification, func.count(Reply.id))
        .filter(*base_filter)
        .group_by(Reply.classification)
        .all()
    )
    by_status = (
        db.query(Reply.status, func.count(Reply.id))
        .filter(*base_filter)
        .group_by(Reply.status)
        .all()
    )
    total = db.query(func.count(Reply.id)).filter(*base_filter).scalar() or 0
    new_count = (
        db.query(func.count(Reply.id))
        .filter(*base_filter, Reply.status == ReplyStatus.new)
        .scalar() or 0
    )

    return {
        "total": total,
        "new_count": new_count,
        "by_classification": {c: n for c, n in by_classification},
        "by_status": {s.value if hasattr(s, "value") else s: n for s, n in by_status},
    }


@router.get("/")
def list_replies(
    campaign_id: Optional[str] = Query(None),
    classification: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List replies with optional filters. Sorted by received_at descending.

    FIX-14: scoped to the current user's campaigns.
    """
    user_cids = _user_campaign_ids(db, current_user)
    if user_cids is not None and not user_cids:
        return {"total": 0, "replies": []}
    if campaign_id and user_cids is not None and campaign_id not in user_cids:
        raise HTTPException(404, "Campaign not found")

    q = db.query(Reply).options(
        joinedload(Reply.prospect),
        joinedload(Reply.campaign),
    )
    if user_cids is not None:
        q = q.filter(Reply.campaign_id.in_(user_cids))

    if campaign_id:
        q = q.filter(Reply.campaign_id == campaign_id)
    if classification:
        q = q.filter(Reply.classification == classification)
    if status:
        q = q.filter(Reply.status == status)
    if date_from:
        try:
            dt = datetime.fromisoformat(date_from)
            q = q.filter(Reply.received_at >= dt)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to)
            q = q.filter(Reply.received_at <= dt)
        except ValueError:
            pass

    total = q.count()
    replies = q.order_by(Reply.received_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "replies": [_reply_to_dict(r) for r in replies],
    }


@router.get("/{reply_id}")
def get_reply(
    reply_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single reply with full details. FIX-14: ownership-checked."""
    user_cids = _user_campaign_ids(db, current_user)
    r = (
        db.query(Reply)
        .options(joinedload(Reply.prospect), joinedload(Reply.campaign))
        .filter(Reply.id == reply_id)
        .first()
    )
    if not r:
        raise HTTPException(status_code=404, detail="Reply not found")
    if user_cids is not None and r.campaign_id not in user_cids:
        raise HTTPException(status_code=404, detail="Reply not found")
    return _reply_to_dict(r)


@router.post("/{reply_id}/action")
def reply_action(
    reply_id: str,
    req: ReplyActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Take action on a reply:
      - meeting_booked: marks reply status (analytics compute meetings dynamically)
      - snooze: sets snoozed status with optional snooze_until date
      - archive: marks as archived

    FIX-14: ownership-checked.
    """
    user_cids = _user_campaign_ids(db, current_user)
    r = db.query(Reply).filter(Reply.id == reply_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Reply not found")
    if user_cids is not None and r.campaign_id not in user_cids:
        raise HTTPException(status_code=404, detail="Reply not found")

    action = req.action.lower()

    if action == "meeting_booked":
        r.status = ReplyStatus.meeting_booked
        # meetings_booked is computed dynamically from Reply.status  -  no static counter to update
        logger.info("reply_action: reply=%s marked meeting_booked", reply_id)

    elif action == "snooze":
        r.status = ReplyStatus.snoozed
        if req.snooze_until:
            try:
                r.snoozed_until = datetime.fromisoformat(req.snooze_until)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid snooze_until date format")
        logger.info("reply_action: reply=%s snoozed until %s", reply_id, r.snoozed_until)

    elif action == "archive":
        r.status = ReplyStatus.archived
        logger.info("reply_action: reply=%s archived", reply_id)

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    db.commit()
    return {"status": "ok", "reply_id": reply_id, "new_status": r.status.value}
