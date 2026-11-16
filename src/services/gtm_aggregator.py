"""GTM Analytics aggregation service.

Queries multiple tables (LinkedInQueue, SendLog, EmailSequence, Reply,
ConferenceLead, GtmManualEntry) and returns monthly-bucketed metrics for
the unified GTM Analytics dashboard.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import func, case, and_, literal_column
from sqlalchemy.orm import Session

from ..db.database import DATABASE_URL
from ..db.models import (
    LinkedInQueue, LinkedInQueueStatus,
    SendLog, EmailSequence, SequenceStatus,
    Reply, ReplyStatus,
    ConferenceLead,
    GtmManualEntry,
)

logger = logging.getLogger(__name__)

_IS_POSTGRES = DATABASE_URL.startswith("postgresql")


def _month_expr(col):
    """SQL expression that extracts 'YYYY-MM' from a datetime column."""
    if _IS_POSTGRES:
        return func.to_char(col, "YYYY-MM")
    return func.strftime("%Y-%m", col)


def _date_filter(col, start: date, end: date):
    """Return a list of filter clauses bounding *col* between start and end (inclusive)."""
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    # end is inclusive — go to end of day
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    return [col >= start_dt, col <= end_dt]


def _month_range(start: date, end: date) -> list[str]:
    """Return list of 'YYYY-MM' strings covering start to end inclusive."""
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def _to_month_dict(rows, months: list[str]) -> dict:
    """Convert [(month_str, count)] rows to {month: count, cumulative: N}."""
    data = {m: 0 for m in months}
    for month_str, count in rows:
        if month_str in data:
            data[month_str] = int(count)
    data["cumulative"] = sum(data[m] for m in months)
    return data


def _manual_sums(db: Session, start: date, end: date, category: str,
                 metric_key: str, user_id: Optional[str] = None,
                 source_ref: Optional[str] = None) -> list:
    """Query GtmManualEntry for a specific category+metric, grouped by month."""
    months = _month_range(start, end)
    q = db.query(
        GtmManualEntry.month,
        func.sum(GtmManualEntry.value),
    ).filter(
        GtmManualEntry.category == category,
        GtmManualEntry.metric_key == metric_key,
        GtmManualEntry.month >= months[0] if months else "0000-00",
        GtmManualEntry.month <= months[-1] if months else "9999-99",
    )
    if user_id:
        q = q.filter(GtmManualEntry.user_id == user_id)
    if source_ref:
        q = q.filter(GtmManualEntry.source_ref == source_ref)
    q = q.group_by(GtmManualEntry.month)
    return q.all()


# ── LinkedIn ─────────────────────────────────────────────────────────────

def aggregate_linkedin(db: Session, start: date, end: date,
                       user_id: Optional[str] = None) -> dict:
    months = _month_range(start, end)
    filters = _date_filter(LinkedInQueue.created_at, start, end)
    if user_id:
        filters.append(LinkedInQueue.user_id == user_id)

    rows = db.query(
        _month_expr(LinkedInQueue.created_at).label("month"),
        LinkedInQueue.action_type,
        func.count(LinkedInQueue.id),
    ).filter(
        LinkedInQueue.status == LinkedInQueueStatus.done,
        *filters,
    ).group_by("month", LinkedInQueue.action_type).all()

    connections = {m: 0 for m in months}
    messages = {m: 0 for m in months}
    for month_str, action_type, cnt in rows:
        if month_str not in connections:
            continue
        if action_type == "connect":
            connections[month_str] = int(cnt)
        elif action_type == "message":
            messages[month_str] = int(cnt)

    # Responses: LinkedIn queue items with result_detail containing acceptance/reply indicators
    resp_rows = db.query(
        _month_expr(LinkedInQueue.processed_at).label("month"),
        func.count(LinkedInQueue.id),
    ).filter(
        LinkedInQueue.status == LinkedInQueueStatus.done,
        LinkedInQueue.result_detail.isnot(None),
        LinkedInQueue.action_type == "message",
        *_date_filter(LinkedInQueue.processed_at, start, end),
        *([LinkedInQueue.user_id == user_id] if user_id else []),
    ).group_by("month").all()
    responses = _to_month_dict(resp_rows, months)

    # Meetings: manual entries
    meetings_rows = _manual_sums(db, start, end, "linkedin", "meetings_booked", user_id)
    meetings = _to_month_dict(meetings_rows, months)

    connections["cumulative"] = sum(connections[m] for m in months)
    messages["cumulative"] = sum(messages[m] for m in months)

    return {
        "connections_sent": connections,
        "messages_sent": messages,
        "responses": responses,
        "meetings_booked": meetings,
    }


# ── Cold Email ───────────────────────────────────────────────────────────

def aggregate_cold_email(db: Session, start: date, end: date,
                         user_id: Optional[str] = None) -> dict:
    months = _month_range(start, end)

    # Sends by step from SendLog
    send_filters = [SendLog.status == "sent", *_date_filter(SendLog.sent_at, start, end)]
    if user_id:
        send_filters.append(SendLog.user_id == user_id)

    send_rows = db.query(
        _month_expr(SendLog.sent_at).label("month"),
        case((SendLog.step == 1, "first"), else_="followup").label("step_type"),
        func.count(SendLog.id),
    ).filter(*send_filters).group_by("month", "step_type").all()

    first_emails = {m: 0 for m in months}
    follow_ups = {m: 0 for m in months}
    for month_str, step_type, cnt in send_rows:
        if month_str not in first_emails:
            continue
        if step_type == "first":
            first_emails[month_str] = int(cnt)
        else:
            follow_ups[month_str] = int(cnt)

    first_emails["cumulative"] = sum(first_emails[m] for m in months)
    follow_ups["cumulative"] = sum(follow_ups[m] for m in months)

    # Opens from EmailSequence
    open_filters = [
        EmailSequence.opened_at.isnot(None),
        *_date_filter(EmailSequence.opened_at, start, end),
    ]
    if user_id:
        from ..db.models import Campaign
        open_filters.append(EmailSequence.campaign_id.in_(
            db.query(Campaign.id).filter(Campaign.user_id == user_id)
        ))
    open_rows = db.query(
        _month_expr(EmailSequence.opened_at).label("month"),
        func.count(EmailSequence.id),
    ).filter(*open_filters).group_by("month").all()
    opened = _to_month_dict(open_rows, months)

    # Bounces from EmailSequence (status=bounced)
    bounce_filters = [
        EmailSequence.status == SequenceStatus.bounced,
        *_date_filter(EmailSequence.created_at, start, end),
    ]
    if user_id:
        from ..db.models import Campaign
        bounce_filters.append(EmailSequence.campaign_id.in_(
            db.query(Campaign.id).filter(Campaign.user_id == user_id)
        ))
    bounce_rows = db.query(
        _month_expr(EmailSequence.created_at).label("month"),
        func.count(EmailSequence.id),
    ).filter(*bounce_filters).group_by("month").all()
    bounced = _to_month_dict(bounce_rows, months)

    # Responses from EmailSequence.replied_at
    reply_filters = [
        EmailSequence.replied_at.isnot(None),
        *_date_filter(EmailSequence.replied_at, start, end),
    ]
    if user_id:
        from ..db.models import Campaign
        reply_filters.append(EmailSequence.campaign_id.in_(
            db.query(Campaign.id).filter(Campaign.user_id == user_id)
        ))
    reply_rows = db.query(
        _month_expr(EmailSequence.replied_at).label("month"),
        func.count(EmailSequence.id),
    ).filter(*reply_filters).group_by("month").all()
    responses = _to_month_dict(reply_rows, months)

    # Meetings from Reply table (status = meeting_booked) + manual overrides
    meeting_filters = [
        Reply.status == ReplyStatus.meeting_booked,
        *_date_filter(Reply.received_at, start, end),
    ]
    if user_id:
        from ..db.models import Campaign
        meeting_filters.append(Reply.campaign_id.in_(
            db.query(Campaign.id).filter(Campaign.user_id == user_id)
        ))
    meeting_rows = db.query(
        _month_expr(Reply.received_at).label("month"),
        func.count(Reply.id),
    ).filter(*meeting_filters).group_by("month").all()
    meetings = _to_month_dict(meeting_rows, months)

    # Add manual meeting overrides
    manual_rows = _manual_sums(db, start, end, "cold_email", "meetings_booked", user_id)
    for month_str, val in manual_rows:
        if month_str in meetings:
            meetings[month_str] += int(val)
    meetings["cumulative"] = sum(meetings[m] for m in months)

    return {
        "first_emails_sent": first_emails,
        "follow_ups_sent": follow_ups,
        "opened": opened,
        "bounced": bounced,
        "responses": responses,
        "meetings_booked": meetings,
    }


# ── Inbound ──────────────────────────────────────────────────────────────

def aggregate_inbound(db: Session, start: date, end: date,
                      user_id: Optional[str] = None) -> dict:
    months = _month_range(start, end)
    rows = _manual_sums(db, start, end, "inbound", "inbound_lead", user_id)
    leads = _to_month_dict(rows, months)
    return {"leads_registered": leads}


# ── Conference ───────────────────────────────────────────────────────────

def aggregate_conference(db: Session, start: date, end: date,
                         user_id: Optional[str] = None,
                         conference_names: Optional[list[str]] = None) -> dict:
    months = _month_range(start, end)
    base_filters = _date_filter(ConferenceLead.created_at, start, end)
    if user_id:
        base_filters.append(ConferenceLead.user_id == user_id)
    if conference_names:
        base_filters.append(ConferenceLead.conference_name.in_(conference_names))

    # Emails sent (draft_created_at IS NOT NULL)
    sent_rows = db.query(
        _month_expr(ConferenceLead.draft_created_at).label("month"),
        func.count(ConferenceLead.id),
    ).filter(
        ConferenceLead.draft_created_at.isnot(None),
        *base_filters,
    ).group_by("month").all()
    emails_sent = _to_month_dict(sent_rows, months)

    # Responses (replied_at IS NOT NULL)
    resp_rows = db.query(
        _month_expr(ConferenceLead.replied_at).label("month"),
        func.count(ConferenceLead.id),
    ).filter(
        ConferenceLead.replied_at.isnot(None),
        *base_filters,
    ).group_by("month").all()
    responses = _to_month_dict(resp_rows, months)

    # Meetings booked
    meet_rows = db.query(
        _month_expr(ConferenceLead.created_at).label("month"),
        func.count(ConferenceLead.id),
    ).filter(
        ConferenceLead.meeting_booked == True,
        *base_filters,
    ).group_by("month").all()
    meetings = _to_month_dict(meet_rows, months)

    # Add manual conference meeting overrides
    for conf_name in (conference_names or [None]):
        manual_rows = _manual_sums(db, start, end, "conference", "meetings_booked",
                                   user_id, source_ref=conf_name)
        for month_str, val in manual_rows:
            if month_str in meetings:
                meetings[month_str] += int(val)
    meetings["cumulative"] = sum(meetings[m] for m in months)

    # Per-conference breakdown
    by_conference = []
    if not conference_names:
        conf_list = [r[0] for r in db.query(ConferenceLead.conference_name).distinct().all()]
    else:
        conf_list = conference_names

    for cname in conf_list:
        c_filters = [*base_filters, ConferenceLead.conference_name == cname]
        c_sent = db.query(func.count(ConferenceLead.id)).filter(
            ConferenceLead.draft_created_at.isnot(None), *c_filters).scalar() or 0
        c_resp = db.query(func.count(ConferenceLead.id)).filter(
            ConferenceLead.replied_at.isnot(None), *c_filters).scalar() or 0
        c_meet = db.query(func.count(ConferenceLead.id)).filter(
            ConferenceLead.meeting_booked == True, *c_filters).scalar() or 0
        by_conference.append({
            "conference_name": cname,
            "emails_sent": c_sent,
            "responses": c_resp,
            "meetings_booked": c_meet,
        })

    return {
        "emails_sent": emails_sent,
        "responses": responses,
        "meetings_booked": meetings,
        "by_conference": by_conference,
    }


# ── Referral ─────────────────────────────────────────────────────────────

def aggregate_referral(db: Session, start: date, end: date,
                       user_id: Optional[str] = None) -> dict:
    months = _month_range(start, end)
    rows = _manual_sums(db, start, end, "referral", "referral_meetings", user_id)
    meetings = _to_month_dict(rows, months)
    return {"meetings_booked": meetings}


# ── Dashboard builder ────────────────────────────────────────────────────

ALL_CATEGORIES = ("linkedin", "cold_email", "inbound", "conference", "referral")


def build_dashboard(db: Session, start: date, end: date,
                    user_id: Optional[str] = None,
                    categories: Optional[list[str]] = None,
                    conference_names: Optional[list[str]] = None) -> dict:
    cats = categories or list(ALL_CATEGORIES)
    months = _month_range(start, end)

    result = {"months": months, "sections": {}}

    if "linkedin" in cats:
        result["sections"]["linkedin"] = aggregate_linkedin(db, start, end, user_id)
    if "cold_email" in cats:
        result["sections"]["cold_email"] = aggregate_cold_email(db, start, end, user_id)
    if "inbound" in cats:
        result["sections"]["inbound"] = aggregate_inbound(db, start, end, user_id)
    if "conference" in cats:
        result["sections"]["conference"] = aggregate_conference(
            db, start, end, user_id, conference_names)
    if "referral" in cats:
        result["sections"]["referral"] = aggregate_referral(db, start, end, user_id)

    # Grand total meetings across all channels
    total_meetings = {m: 0 for m in months}
    for section in result["sections"].values():
        mb = section.get("meetings_booked")
        if mb:
            for m in months:
                total_meetings[m] += mb.get(m, 0)
    total_meetings["cumulative"] = sum(total_meetings[m] for m in months)
    result["grand_total_meetings"] = total_meetings

    return result
