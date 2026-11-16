"""
Campaign analytics API — performance metrics, cost tracking, and domain health monitoring.

Provides read-only analytics endpoints for the outreach dashboard. All stats are
computed from the local DB (not from external APIs) for speed and independence.

Endpoints:
  GET /analytics/overview         — Aggregated KPIs + per-campaign breakdown
  GET /analytics/ab-test/{id}     — A/B test variant comparison for a campaign
  GET /analytics/credits          — API credit consumption per provider (last N days)
  GET /analytics/users            — Per-user resource usage breakdown (admin only)
  GET /analytics/costs            — Subscription + Anthropic token cost breakdown
  GET /analytics/insights         — Subject line performance, best send times, funnel
  GET /analytics/domain-health    — Per-sending-domain bounce/open rates + health status
  POST /analytics/domain-health/check — Active warnings for at-risk/critical domains
  GET /analytics/domain-health-history — Historical snapshots for trend charts
  GET /analytics/segments         — Per-segment performance breakdown
  GET /analytics/sequence-stages  — Step-by-step drop-off analysis
  GET /analytics/cost-per-meeting — Total cost / total meetings booked
  GET /analytics/channel-attribution — Which sequence step drove each reply

Security:
  Non-admin users are automatically scoped to their own data via _scope_user_id().
  Admins can pass user_id to inspect another rep, or omit it for system-wide view.
  The /users endpoint is admin-only (leaks every rep's spend).

Key metrics:
  - emails_sent/opened/replied derived from EmailSequence status fields
  - meetings_booked and positive_replies derived from Reply table (dynamic, not cached)
  - Domain health thresholds: healthy <=5% bounce, at_risk 5-10%, critical >10%
"""

import uuid
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, extract, case
from sqlalchemy.orm import Session
from ..db.database import get_db
from ..db.models import (
    Campaign, CampaignStatus, EmailSequence, SequenceStatus,
    AppSetting, User, Prospect, ProspectStatus, SendLog,
    AudienceSegment, DomainHealthSnapshot, Reply, ReplyStatus,
)
from ..config import MODEL_SMART, get_model_pricing
from .auth import get_current_user
from .settings import (
    COST_APOLLO_MONTHLY_KEY,
    COST_PHANTOMBUSTER_MONTHLY_KEY, COST_REACHER_MONTHLY_KEY,
    COST_HUNTER_MONTHLY_KEY,
    COST_TAVILY_MONTHLY_KEY,
    _get_float_setting,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])
logger = logging.getLogger(__name__)


def _scope_user_id(current_user: "User | None", requested_user_id: Optional[str]) -> Optional[str]:
    """
    FIX-8: Resolve which user_id the analytics query should be scoped to.

    Non-admins are forced to their own id regardless of the query param.
    Admins may pass an explicit user_id to inspect another rep, or omit it
    to see system-wide totals.
    """
    if current_user is None:
        return requested_user_id
    is_admin = bool(current_user.role and current_user.role.value == "admin")
    if is_admin:
        return requested_user_id  # may be None  -  admin sees everything
    return current_user.id


@router.get("/overview")
def get_overview(
    user_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns aggregated KPIs across all campaigns, plus a per-campaign breakdown.
    Stats are derived from local DB counts (enrolled/opened/replied sequences).

    FIX-8: non-admins always see only their own campaigns regardless of the
    query param. Admins may pass user_id to view another rep.
    """
    user_id = _scope_user_id(current_user, user_id)
    q = db.query(Campaign)
    if user_id:
        q = q.filter(Campaign.user_id == user_id)
    campaigns = q.all()

    totals = {
        "emails_sent": 0,
        "delivered": 0,
        "opened": 0,
        "replied": 0,
        "positive_replies": 0,
        "meetings_booked": 0,
    }

    campaign_rows = []

    # Pre-compute local DB stats for ALL campaigns in two SQL queries (no N+1).
    # Count enrolled/opened/replied per campaign using SQL aggregations.
    pending_val = SequenceStatus.pending.value
    local_sent = {
        row[0]: row[1]
        for row in db.query(EmailSequence.campaign_id, func.count(EmailSequence.id))
        .filter(EmailSequence.status != pending_val)
        .group_by(EmailSequence.campaign_id)
        .all()
    }
    local_opened = {
        row[0]: row[1]
        for row in db.query(EmailSequence.campaign_id, func.count(EmailSequence.id))
        .filter(EmailSequence.opened_at.isnot(None))
        .group_by(EmailSequence.campaign_id)
        .all()
    }
    local_replied = {
        row[0]: row[1]
        for row in db.query(EmailSequence.campaign_id, func.count(EmailSequence.id))
        .filter(EmailSequence.replied_at.isnot(None))
        .group_by(EmailSequence.campaign_id)
        .all()
    }

    # Dynamic meetings_booked: count replies with status=meeting_booked per campaign
    dynamic_meetings = {
        row[0]: row[1]
        for row in db.query(Reply.campaign_id, func.count(Reply.id))
        .filter(Reply.status == ReplyStatus.meeting_booked)
        .group_by(Reply.campaign_id)
        .all()
    }
    # Dynamic positive_replies: count replies classified as positive per campaign
    dynamic_positive = {
        row[0]: row[1]
        for row in db.query(Reply.campaign_id, func.count(Reply.id))
        .filter(Reply.classification == "positive")
        .group_by(Reply.campaign_id)
        .all()
    }

    for c in campaigns:
        sent = local_sent.get(c.id, 0)
        opened = local_opened.get(c.id, 0)
        replied = local_replied.get(c.id, 0)
        positive = dynamic_positive.get(c.id, 0)
        meetings = dynamic_meetings.get(c.id, 0)

        row = {
            "id": c.id,
            "user_id": c.user_id,
            "user_name": c.user.name if c.user_id and hasattr(c, "user") and c.user else None,
            "name": c.name,
            "status": c.status.value if c.status else "draft",
            "launched_at": c.launched_at.isoformat() if c.launched_at else None,
            "ab_test_enabled": bool(c.ab_test_enabled),
            "emails_sent": sent,
            "delivered": sent,
            "opened": opened,
            "replied": replied,
            "positive_replies": positive,
            "meetings_booked": meetings,
            "delivery_rate": None,
            "open_rate": round(opened / sent * 100, 1) if sent else None,
            "reply_rate": round(replied / sent * 100, 1) if sent else None,
            "positive_reply_rate": round(positive / sent * 100, 1) if sent and positive else None,
        }

        totals["emails_sent"] += sent
        totals["delivered"] += sent
        totals["opened"] += opened
        totals["replied"] += replied
        totals["positive_replies"] += positive
        totals["meetings_booked"] += meetings

        campaign_rows.append(row)

    # Compute aggregate rates
    agg_delivery = round(totals["delivered"] / totals["emails_sent"] * 100, 1) if totals["emails_sent"] else None
    agg_open = round(totals["opened"] / totals["delivered"] * 100, 1) if totals["delivered"] else None
    agg_reply = round(totals["replied"] / totals["delivered"] * 100, 1) if totals["delivered"] else None
    agg_pos_reply = round(totals["positive_replies"] / totals["emails_sent"] * 100, 1) if totals["emails_sent"] else None

    return {
        "totals": {
            **totals,
            "delivery_rate": agg_delivery,
            "open_rate": agg_open,
            "reply_rate": agg_reply,
            "positive_reply_rate": agg_pos_reply,
        },
        "campaigns": campaign_rows,
    }


# Manual metrics update removed  -  meetings_booked and positive_replies
# are now computed dynamically from the Reply table.


@router.get("/ab-test/{campaign_id}")
def ab_test_results(campaign_id: str, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """
    Returns A/B test performance for a campaign.
    Compares open rates between variant A and variant B subject lines.
    """
    campaign = db.query(Campaign).filter_by(id=campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if campaign.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your campaign.")
    if not campaign.ab_test_enabled:
        raise HTTPException(status_code=400, detail="A/B testing is not enabled for this campaign.")

    sequences = (
        db.query(EmailSequence)
        .filter(
            EmailSequence.campaign_id == campaign_id,
            EmailSequence.email1_subject_b.isnot(None),
        )
        .all()
    )

    if not sequences:
        return {
            "campaign_id": campaign_id,
            "total_sequences": 0,
            "variant_a": {"sent": 0, "opened": 0, "open_rate": None},
            "variant_b": {"sent": 0, "opened": 0, "open_rate": None},
            "winner": None,
        }

    a_sent = 0
    a_opened = 0
    b_sent = 0
    b_opened = 0

    for seq in sequences:
        if seq.variant_sent == "B":
            b_sent += 1
            if seq.opened_at:
                b_opened += 1
        else:
            # Default to A (includes variant_sent=None for pre-A/B sequences)
            a_sent += 1
            if seq.opened_at:
                a_opened += 1

    a_rate = round(a_opened / a_sent * 100, 1) if a_sent else None
    b_rate = round(b_opened / b_sent * 100, 1) if b_sent else None

    winner = None
    if a_rate is not None and b_rate is not None:
        if a_rate > b_rate:
            winner = "A"
        elif b_rate > a_rate:
            winner = "B"
        else:
            winner = "tie"

    return {
        "campaign_id": campaign_id,
        "campaign_name": campaign.name,
        "total_sequences": len(sequences),
        "variant_a": {
            "sent": a_sent,
            "opened": a_opened,
            "open_rate": a_rate,
            "sample_subjects": list({s.email1_subject for s in sequences if s.variant_sent != "B" and s.email1_subject}),
        },
        "variant_b": {
            "sent": b_sent,
            "opened": b_opened,
            "open_rate": b_rate,
            "sample_subjects": list({s.email1_subject_b for s in sequences if s.email1_subject_b}),
        },
        "winner": winner,
    }


@router.get("/credits")
def credit_usage(
    days: int = 30,
    user_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """Returns API credit consumption totals per provider for the last N days.

    FIX-8: non-admins forced to their own user_id."""
    from ..db.credits import get_summary
    user_id = _scope_user_id(current_user, user_id)
    return {"days": days, "usage": get_summary(days, user_id=user_id)}


@router.get("/users")
def user_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Per-user breakdown: campaigns, prospects, token usage, credit usage.

    FIX-8: admin only  -  leaks every rep's spend if exposed broadly.
    """
    is_admin = bool(current_user and current_user.role and current_user.role.value == "admin")
    if current_user and not is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    from ..db.credits import get_summary_by_user
    pricing = get_model_pricing(MODEL_SMART)
    inp_rate = pricing["input"]
    out_rate = pricing["output"]

    users = db.query(User).order_by(User.created_at).all()
    credit_by_user = get_summary_by_user(days=30)

    result = []
    for u in users:
        campaigns = db.query(Campaign).filter_by(user_id=u.id).all()
        total_inp = sum(c.tokens_input or 0 for c in campaigns)
        total_out = sum(c.tokens_output or 0 for c in campaigns)
        anthropic_cost = (total_inp / 1_000_000 * inp_rate) + (total_out / 1_000_000 * out_rate)

        result.append({
            "user_id": u.id,
            "name": u.name,
            "email": u.email,
            "role": u.role.value if u.role else "rep",
            "campaigns_count": len(campaigns),
            "active_campaigns": sum(1 for c in campaigns if c.status == CampaignStatus.active),
            "tokens_input": total_inp,
            "tokens_output": total_out,
            "anthropic_cost_usd": round(anthropic_cost, 4),
            "credit_usage_30d": credit_by_user.get(u.id, {}),
        })

    return result


@router.get("/costs")
def get_costs(
    user_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = _scope_user_id(current_user, user_id)
    """
    Returns cost breakdown: subscription costs + Anthropic token costs per campaign.
    Subscription costs are manually entered by the user.
    Token costs are tracked automatically during email generation.
    """
    pricing = get_model_pricing(MODEL_SMART)
    inp_rate = pricing["input"]
    out_rate = pricing["output"]
    apollo_monthly = _get_float_setting(db, COST_APOLLO_MONTHLY_KEY, 0.0)
    phantombuster_monthly = _get_float_setting(db, COST_PHANTOMBUSTER_MONTHLY_KEY, 0.0)
    reacher_monthly = _get_float_setting(db, COST_REACHER_MONTHLY_KEY, 0.0)
    hunter_monthly = _get_float_setting(db, COST_HUNTER_MONTHLY_KEY, 0.0)
    proxycurl_monthly = 0.0  # Proxycurl removed (shut down 2025)
    tavily_monthly = _get_float_setting(db, COST_TAVILY_MONTHLY_KEY, 0.0)

    q = db.query(Campaign)
    if user_id:
        q = q.filter(Campaign.user_id == user_id)
    campaigns = q.order_by(Campaign.created_at.desc()).all()

    total_inp = sum(c.tokens_input or 0 for c in campaigns)
    total_out = sum(c.tokens_output or 0 for c in campaigns)
    total_anthropic_cost = (total_inp / 1_000_000 * inp_rate) + (total_out / 1_000_000 * out_rate)

    campaign_costs = [
        {
            "id": c.id,
            "name": c.name,
            "status": c.status.value if c.status else "draft",
            "tokens_input": c.tokens_input or 0,
            "tokens_output": c.tokens_output or 0,
            "anthropic_cost_usd": round(
                (c.tokens_input or 0) / 1_000_000 * inp_rate
                + (c.tokens_output or 0) / 1_000_000 * out_rate,
                4,
            ),
        }
        for c in campaigns
    ]

    return {
        "subscriptions": {
            "apollo_monthly": apollo_monthly,
            "phantombuster_monthly": phantombuster_monthly,
            "reacher_monthly": reacher_monthly,
            "hunter_monthly": hunter_monthly,
            "proxycurl_monthly": proxycurl_monthly,
            "tavily_monthly": tavily_monthly,
            "total_monthly": round(apollo_monthly + phantombuster_monthly + reacher_monthly + hunter_monthly + proxycurl_monthly + tavily_monthly, 2),
        },
        "anthropic": {
            "model": MODEL_SMART,
            "total_tokens_input": total_inp,
            "total_tokens_output": total_out,
            "total_cost_usd": round(total_anthropic_cost, 4),
            "pricing": {
                "input_per_mtok": inp_rate,
                "output_per_mtok": out_rate,
            },
        },
        "campaigns": campaign_costs,
    }


@router.get("/insights")
def get_insights(
    user_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = _scope_user_id(current_user, user_id)
    """
    Enhanced analytics: subject line performance, best send times,
    time to first reply, and full funnel metrics. Optionally scoped
    to a single campaign via campaign_id.
    """

    # ── Scope campaigns to user / single campaign if requested ──────────────
    campaign_q = db.query(Campaign)
    if user_id:
        campaign_q = campaign_q.filter(Campaign.user_id == user_id)
    if campaign_id:
        campaign_q = campaign_q.filter(Campaign.id == campaign_id)
    campaigns = campaign_q.all()
    campaign_ids = [c.id for c in campaigns]

    if not campaign_ids:
        return {
            "subject_line_performance": {"top": [], "bottom": []},
            "best_send_times": {},
            "time_to_first_reply": {"per_campaign": [], "overall_avg_days": None},
            "funnel": [],
        }

    # ── 1. Subject Line Performance ─────────────────────────────────────────
    # Group sequences by (campaign_id, email1_subject), count opens / total
    seq_q = (
        db.query(
            EmailSequence.campaign_id,
            EmailSequence.email1_subject,
            func.count(EmailSequence.id).label("total"),
            func.sum(case((EmailSequence.opened_at.isnot(None), 1), else_=0)).label("open_count"),
        )
        .filter(
            EmailSequence.campaign_id.in_(campaign_ids),
            EmailSequence.email1_subject.isnot(None),
            EmailSequence.status != SequenceStatus.pending.value,
        )
        .group_by(EmailSequence.campaign_id, EmailSequence.email1_subject)
        .all()
    )

    campaign_name_map = {c.id: c.name for c in campaigns}
    subject_rows = []
    for row in seq_q:
        total = row.total or 0
        opens = row.open_count or 0
        if total == 0:
            continue
        subject_rows.append({
            "campaign_id": row.campaign_id,
            "campaign_name": campaign_name_map.get(row.campaign_id, ""),
            "subject": row.email1_subject,
            "sent": total,
            "open_count": opens,
            "open_rate": round(opens / total * 100, 1),
        })

    # Sort by open_rate descending; require at least 1 send
    sorted_subjects = sorted(subject_rows, key=lambda r: r["open_rate"], reverse=True)
    top_subjects = sorted_subjects[:5]
    bottom_subjects = sorted_subjects[-5:][::-1] if len(sorted_subjects) > 5 else sorted_subjects[::-1][:5]

    # ── 2. Best Send Times ──────────────────────────────────────────────────
    # From SendLog, group by day_of_week (0=Mon..6=Sun) and hour
    DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    send_logs = (
        db.query(SendLog)
        .filter(SendLog.campaign_id.in_(campaign_ids))
        .all()
    )

    # Also grab sequences for open/reply lookup keyed by sequence_id
    seq_by_id = {}
    if send_logs:
        seq_ids = list({sl.sequence_id for sl in send_logs})
        for seq in db.query(EmailSequence).filter(EmailSequence.id.in_(seq_ids)).all():
            seq_by_id[seq.id] = seq

    heatmap: dict = defaultdict(lambda: defaultdict(lambda: {"sent": 0, "opened": 0, "replied": 0}))
    for sl in send_logs:
        if not sl.sent_at:
            continue
        dow = sl.sent_at.weekday()  # 0=Mon
        hour = sl.sent_at.hour
        day_name = DAY_NAMES[dow]
        heatmap[day_name][str(hour)]["sent"] += 1
        seq = seq_by_id.get(sl.sequence_id)
        if seq and seq.opened_at:
            heatmap[day_name][str(hour)]["opened"] += 1
        if seq and seq.replied_at:
            heatmap[day_name][str(hour)]["replied"] += 1

    # Convert defaultdict to regular dict for JSON serialisation
    heatmap_out = {day: dict(hours) for day, hours in heatmap.items()}

    # ── 3. Time to First Reply ──────────────────────────────────────────────
    # Batch query: fetch all replied sequences across all campaigns in one query
    campaign_ids = [c.id for c in campaigns]
    campaign_map = {c.id: c for c in campaigns}
    all_replied_seqs = (
        db.query(EmailSequence)
        .filter(
            EmailSequence.campaign_id.in_(campaign_ids),
            EmailSequence.replied_at.isnot(None),
        )
        .all()
    ) if campaign_ids else []

    # Group by campaign_id in Python
    _replied_by_campaign: dict[str, list] = defaultdict(list)
    for seq in all_replied_seqs:
        _replied_by_campaign[seq.campaign_id].append(seq)

    reply_times: list[dict] = []
    for cid, replied_seqs in _replied_by_campaign.items():
        c = campaign_map.get(cid)
        if not c or not c.created_at:
            continue
        days_list = []
        for seq in replied_seqs:
            delta = (seq.replied_at - c.created_at).total_seconds() / 86400.0
            if delta >= 0:
                days_list.append(delta)
        if days_list:
            avg = sum(days_list) / len(days_list)
            reply_times.append({
                "campaign_id": c.id,
                "campaign_name": c.name,
                "avg_days": round(avg, 2),
                "replies_count": len(days_list),
            })

    overall_avg = None
    if reply_times:
        total_days = sum(r["avg_days"] * r["replies_count"] for r in reply_times)
        total_replies = sum(r["replies_count"] for r in reply_times)
        overall_avg = round(total_days / total_replies, 2) if total_replies else None

    # ── 4. Funnel Metrics ───────────────────────────────────────────────────
    # Batch-fetch all sequences across campaigns in one query
    all_funnel_seqs = (
        db.query(EmailSequence)
        .filter(EmailSequence.campaign_id.in_(campaign_ids))
        .all()
    ) if campaign_ids else []
    _seqs_by_campaign: dict[str, list] = defaultdict(list)
    for seq in all_funnel_seqs:
        _seqs_by_campaign[seq.campaign_id].append(seq)

    funnel_results = []
    for c in campaigns:
        # Count prospects linked to this campaign
        prospect_ids = c.prospect_ids or []
        total_prospects = len(prospect_ids) if prospect_ids else 0
        if not total_prospects and c.segment_id:
            total_prospects = db.query(func.count(Prospect.id)).filter(
                Prospect.segment_id == c.segment_id
            ).scalar() or 0

        seqs = _seqs_by_campaign.get(c.id, [])
        generated = len(seqs)
        approved = sum(1 for s in seqs if s.approved)
        enrolled = sum(1 for s in seqs if s.status and s.status.value in (
            "enrolled", "opened", "replied", "bounced", "unsubscribed", "connected", "messaged"
        ))
        sent = sum(1 for s in seqs if s.status and s.status.value != "pending")
        opened = sum(1 for s in seqs if s.opened_at is not None)
        replied = sum(1 for s in seqs if s.replied_at is not None)
        positive = db.query(func.count(Reply.id)).filter(
            Reply.campaign_id == c.id, Reply.classification == "positive"
        ).scalar() or 0
        meetings = db.query(func.count(Reply.id)).filter(
            Reply.campaign_id == c.id, Reply.status == ReplyStatus.meeting_booked
        ).scalar() or 0

        stages = [
            ("total_prospects", total_prospects),
            ("generated", generated),
            ("approved", approved),
            ("enrolled", enrolled),
            ("sent", sent),
            ("opened", opened),
            ("replied", replied),
            ("positive_reply", positive),
            ("meeting_booked", meetings),
        ]

        funnel_stages = []
        for i, (name, count) in enumerate(stages):
            prev_count = stages[i - 1][1] if i > 0 else None
            conversion = round(count / prev_count * 100, 1) if prev_count and prev_count > 0 else None
            funnel_stages.append({
                "stage": name,
                "count": count,
                "conversion_from_prev": conversion,
            })

        funnel_results.append({
            "campaign_id": c.id,
            "campaign_name": c.name,
            "stages": funnel_stages,
        })

    return {
        "subject_line_performance": {"top": top_subjects, "bottom": bottom_subjects},
        "best_send_times": heatmap_out,
        "time_to_first_reply": {"per_campaign": reply_times, "overall_avg_days": overall_avg},
        "funnel": funnel_results,
    }


# ---------------------------------------------------------------------------
# Domain Health Monitoring
# ---------------------------------------------------------------------------

def _extract_domain(email: str) -> str:
    """Extract domain from an email address, lowercased."""
    if "@" in email:
        return email.split("@", 1)[1].lower().strip()
    return email.lower().strip()


def _compute_domain_health(db: Session) -> list[dict]:
    """Build per-domain health records for all sending domains.

    Sending domains are discovered from two sources:
      1. Campaign.inbox_ids — JSON array of email addresses configured for each campaign
      2. SendLog.inbox_email — actual sending records (more accurate)

    For each domain, computes:
      - total_sent: emails sent from this domain
      - bounced_count: max of (EmailSequence bounced status, Prospect bounced status)
      - opened_count: emails opened from this domain
      - bounce_rate / open_rate: as percentages
      - status: healthy (<=5%), at_risk (5-10%), critical (>10% bounce rate)

    These thresholds matter because high bounce rates damage sender reputation,
    leading email providers to classify all mail from that domain as spam.
    """

    # 1. Collect all sending domains from campaigns + send logs
    domain_campaigns: dict[str, set[str]] = defaultdict(set)  # domain -> campaign_ids

    # From Campaign.inbox_ids (JSON array of email addresses)
    campaigns = db.query(Campaign).filter(Campaign.inbox_ids.isnot(None)).all()
    for c in campaigns:
        inbox_list = c.inbox_ids if isinstance(c.inbox_ids, list) else []
        for email in inbox_list:
            if email and "@" in str(email):
                domain = _extract_domain(str(email))
                domain_campaigns[domain].add(c.id)

    # From SendLog.inbox_email (actual sending records)
    send_log_domains = (
        db.query(SendLog.inbox_email, SendLog.campaign_id)
        .filter(SendLog.inbox_email.isnot(None))
        .all()
    )
    for inbox_email, campaign_id in send_log_domains:
        if inbox_email and "@" in inbox_email:
            domain = _extract_domain(inbox_email)
            domain_campaigns[domain].add(campaign_id)

    if not domain_campaigns:
        return []

    # 2. For each domain, aggregate stats across its campaigns
    results = []
    bounced_val = SequenceStatus.bounced.value
    opened_val = SequenceStatus.opened.value

    for domain, campaign_ids in sorted(domain_campaigns.items()):
        campaign_id_list = list(campaign_ids)

        # Filter to only this domain's send logs
        total_sent = 0
        all_send_logs = (
            db.query(SendLog)
            .filter(SendLog.campaign_id.in_(campaign_id_list))
            .filter(SendLog.inbox_email.isnot(None))
            .all()
        )
        for sl in all_send_logs:
            if sl.inbox_email and _extract_domain(sl.inbox_email) == domain:
                total_sent += 1

        # If no send logs, fall back to counting EmailSequences for these campaigns
        if total_sent == 0:
            total_sent = (
                db.query(func.count(EmailSequence.id))
                .filter(
                    EmailSequence.campaign_id.in_(campaign_id_list),
                    EmailSequence.status != SequenceStatus.pending.value,
                )
                .scalar()
            ) or 0

        # Bounced: count EmailSequence with bounced status in these campaigns
        bounced_count = (
            db.query(func.count(EmailSequence.id))
            .filter(
                EmailSequence.campaign_id.in_(campaign_id_list),
                EmailSequence.status == bounced_val,
            )
            .scalar()
        ) or 0

        # Also count bounced prospects linked to these campaigns
        bounced_prospects = (
            db.query(func.count(Prospect.id))
            .join(EmailSequence, EmailSequence.prospect_id == Prospect.id)
            .filter(
                EmailSequence.campaign_id.in_(campaign_id_list),
                Prospect.status == ProspectStatus.bounced.value,
            )
            .scalar()
        ) or 0

        # Use the higher of the two bounce signals
        bounced_count = max(bounced_count, bounced_prospects)

        # Opened: count EmailSequence with opened_at set in these campaigns
        opened_count = (
            db.query(func.count(EmailSequence.id))
            .filter(
                EmailSequence.campaign_id.in_(campaign_id_list),
                EmailSequence.opened_at.isnot(None),
            )
            .scalar()
        ) or 0

        # Compute rates
        bounce_rate = round(bounced_count / total_sent * 100, 2) if total_sent > 0 else 0.0
        open_rate = round(opened_count / total_sent * 100, 2) if total_sent > 0 else 0.0

        # Determine status
        if bounce_rate > 10:
            status = "critical"
        elif bounce_rate > 5:
            status = "at_risk"
        else:
            status = "healthy"

        results.append({
            "domain": domain,
            "total_sent": total_sent,
            "bounced_count": bounced_count,
            "bounce_rate": bounce_rate,
            "opened_count": opened_count,
            "open_rate": open_rate,
            "status": status,
            "campaign_count": len(campaign_id_list),
        })

    # Sort: critical first, then at_risk, then healthy
    status_order = {"critical": 0, "at_risk": 1, "healthy": 2}
    results.sort(key=lambda r: (status_order.get(r["status"], 3), -r["total_sent"]))

    return results


@router.get("/domain-health")
def get_domain_health(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Returns per-sending-domain health metrics: bounce rate, open rate, and status.
    Status thresholds:
      - healthy:  bounce_rate <= 5%
      - at_risk:  bounce_rate > 5% and <= 10%
      - critical: bounce_rate > 10%
    Also saves a snapshot of each domain's health for historical tracking.
    """
    domains = _compute_domain_health(db)

    # Save snapshots for history  -  only if the last snapshot for this domain
    # is older than 1 hour to prevent unbounded row growth on repeated GETs.
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    for d in domains:
        recent = (
            db.query(DomainHealthSnapshot)
            .filter(
                DomainHealthSnapshot.domain == d["domain"],
                DomainHealthSnapshot.recorded_at >= one_hour_ago,
            )
            .first()
        )
        if recent:
            continue
        snapshot = DomainHealthSnapshot(
            id=str(uuid.uuid4()),
            domain=d["domain"],
            bounce_rate=d["bounce_rate"],
            total_sent=d["total_sent"],
            total_bounced=d["bounced_count"],
        )
        db.add(snapshot)
    db.commit()

    has_critical = any(d["status"] == "critical" for d in domains)
    has_at_risk = any(d["status"] == "at_risk" for d in domains)

    if has_critical:
        overall_status = "critical"
    elif has_at_risk:
        overall_status = "at_risk"
    else:
        overall_status = "healthy"

    return {
        "overall_status": overall_status,
        "domain_count": len(domains),
        "domains": domains,
    }


@router.post("/domain-health/check")
def check_domain_health(db: Session = Depends(get_db)):
    """
    Checks all active campaigns for domain health issues.
    Returns warnings for domains exceeding bounce thresholds.
    Intended to be called by the scheduler or before campaign launch.
    """
    domains = _compute_domain_health(db)
    warnings = []

    # Build a map of domain -> affected active campaigns
    active_campaigns = (
        db.query(Campaign)
        .filter(Campaign.status == CampaignStatus.active)
        .all()
    )

    domain_active_campaigns: dict[str, list[str]] = defaultdict(list)
    for c in active_campaigns:
        inbox_list = c.inbox_ids if isinstance(c.inbox_ids, list) else []
        for email in inbox_list:
            if email and "@" in str(email):
                domain = _extract_domain(str(email))
                domain_active_campaigns[domain].append(c.name or c.id)

    for d in domains:
        if d["status"] == "critical":
            affected = domain_active_campaigns.get(d["domain"], [])
            warning = {
                "level": "critical",
                "domain": d["domain"],
                "bounce_rate": d["bounce_rate"],
                "total_sent": d["total_sent"],
                "bounced_count": d["bounced_count"],
                "message": (
                    f"Domain {d['domain']} has a {d['bounce_rate']}% bounce rate "
                    f"({d['bounced_count']}/{d['total_sent']} bounced). "
                    f"Consider pausing campaigns using this domain."
                ),
            }
            if affected:
                warning["affected_campaigns"] = affected
            warnings.append(warning)
            logger.warning(
                f"DOMAIN HEALTH CRITICAL: {d['domain']} bounce_rate={d['bounce_rate']}% "
                f"({d['bounced_count']}/{d['total_sent']})"
            )
        elif d["status"] == "at_risk":
            affected = domain_active_campaigns.get(d["domain"], [])
            warning = {
                "level": "warning",
                "domain": d["domain"],
                "bounce_rate": d["bounce_rate"],
                "total_sent": d["total_sent"],
                "bounced_count": d["bounced_count"],
                "message": (
                    f"Domain {d['domain']} has a {d['bounce_rate']}% bounce rate. "
                    f"Monitor closely  -  risk of deliverability issues."
                ),
            }
            if affected:
                warning["affected_campaigns"] = affected
            warnings.append(warning)
            logger.warning(
                f"DOMAIN HEALTH AT RISK: {d['domain']} bounce_rate={d['bounce_rate']}% "
                f"({d['bounced_count']}/{d['total_sent']})"
            )

    return {
        "checked": len(domains),
        "warnings_count": len(warnings),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Domain Health History
# ---------------------------------------------------------------------------

@router.get("/domain-health-history")
def get_domain_health_history(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns domain health snapshots for the last N days, grouped by domain."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    snapshots = (
        db.query(DomainHealthSnapshot)
        .filter(DomainHealthSnapshot.recorded_at >= cutoff)
        .order_by(DomainHealthSnapshot.recorded_at.desc())
        .all()
    )

    by_domain: dict[str, list] = defaultdict(list)
    for s in snapshots:
        by_domain[s.domain].append({
            "id": s.id,
            "bounce_rate": s.bounce_rate,
            "total_sent": s.total_sent,
            "total_bounced": s.total_bounced,
            "recorded_at": s.recorded_at.isoformat() if s.recorded_at else None,
        })

    return {
        "days": days,
        "domains": dict(by_domain),
    }


# ---------------------------------------------------------------------------
# Segment Performance
# ---------------------------------------------------------------------------

@router.get("/segments")
def segment_performance(
    user_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = _scope_user_id(current_user, user_id)
    """
    Per-segment breakdown: total prospects, emails sent, opened, replied,
    meetings booked, reply rate, and token cost.
    """
    segments = db.query(AudienceSegment).all()
    pricing = get_model_pricing(MODEL_SMART)
    inp_rate = pricing["input"]
    out_rate = pricing["output"]

    results = []
    for seg in segments:
        # Prospects in this segment
        prospect_count = (
            db.query(func.count(Prospect.id))
            .filter(Prospect.segment_id == seg.id)
            .scalar()
        ) or 0

        # Campaigns linked to this segment
        camp_q = db.query(Campaign).filter(Campaign.segment_id == seg.id)
        if user_id:
            camp_q = camp_q.filter(Campaign.user_id == user_id)
        campaigns = camp_q.all()
        campaign_ids = [c.id for c in campaigns]

        if not campaign_ids:
            results.append({
                "segment_id": seg.id,
                "segment_name": seg.name,
                "total_prospects": prospect_count,
                "emails_sent": 0,
                "opened": 0,
                "replied": 0,
                "meetings_booked": 0,
                "reply_rate": None,
                "token_cost_usd": 0.0,
            })
            continue

        # Sequence stats for these campaigns
        sent = (
            db.query(func.count(EmailSequence.id))
            .filter(
                EmailSequence.campaign_id.in_(campaign_ids),
                EmailSequence.status != SequenceStatus.pending.value,
            )
            .scalar()
        ) or 0

        opened = (
            db.query(func.count(EmailSequence.id))
            .filter(
                EmailSequence.campaign_id.in_(campaign_ids),
                EmailSequence.opened_at.isnot(None),
            )
            .scalar()
        ) or 0

        replied = (
            db.query(func.count(EmailSequence.id))
            .filter(
                EmailSequence.campaign_id.in_(campaign_ids),
                EmailSequence.replied_at.isnot(None),
            )
            .scalar()
        ) or 0

        meetings = db.query(func.count(Reply.id)).filter(
            Reply.campaign_id.in_(campaign_ids),
            Reply.status == ReplyStatus.meeting_booked,
        ).scalar() or 0
        total_inp = sum(c.tokens_input or 0 for c in campaigns)
        total_out = sum(c.tokens_output or 0 for c in campaigns)
        token_cost = (total_inp / 1_000_000 * inp_rate) + (total_out / 1_000_000 * out_rate)

        results.append({
            "segment_id": seg.id,
            "segment_name": seg.name,
            "total_prospects": prospect_count,
            "emails_sent": sent,
            "opened": opened,
            "replied": replied,
            "meetings_booked": meetings,
            "reply_rate": round(replied / sent * 100, 1) if sent else None,
            "token_cost_usd": round(token_cost, 4),
        })

    return results


# ---------------------------------------------------------------------------
# Sequence Stage Drop-off
# ---------------------------------------------------------------------------

@router.get("/sequence-stages")
def sequence_stage_dropoff(
    campaign_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    For each email step (1-6 + LinkedIn): how many were sent, opened, replied.
    Shows where prospects drop off in the sequence.
    """
    send_q = db.query(SendLog)
    if campaign_id:
        send_q = send_q.filter(SendLog.campaign_id == campaign_id)

    # Group send logs by step
    step_sent = (
        send_q.with_entities(SendLog.step, func.count(SendLog.id))
        .group_by(SendLog.step)
        .all()
    )
    step_sent_map = {row[0]: row[1] for row in step_sent}

    # For opens and replies, we need to join with sequences
    # A sequence that opened/replied at step N means all steps <= current_step were sent
    seq_q = db.query(EmailSequence)
    if campaign_id:
        seq_q = seq_q.filter(EmailSequence.campaign_id == campaign_id)
    sequences = seq_q.all()

    # Count opens and replies per step.
    # current_step = "next step to send", so last sent step = current_step - 1.
    step_opened: dict[int, int] = defaultdict(int)
    step_replied: dict[int, int] = defaultdict(int)
    for seq in sequences:
        last_sent = max(1, (seq.current_step or 1) - 1)
        if seq.opened_at:
            step_opened[last_sent] += 1
        if seq.replied_at:
            step_replied[last_sent] += 1

    # Build results for steps 1-6 + 7 (LinkedIn)
    STEP_LABELS = {1: "Email 1", 2: "Email 2", 3: "Email 3", 4: "Email 4",
                   5: "Email 5", 6: "Email 6", 7: "LinkedIn"}
    stages = []
    for step_num in range(1, 8):
        sent = step_sent_map.get(step_num, 0)
        opened = step_opened.get(step_num, 0)
        replied = step_replied.get(step_num, 0)
        stages.append({
            "step": step_num,
            "label": STEP_LABELS.get(step_num, f"Step {step_num}"),
            "sent": sent,
            "opened": opened,
            "replied": replied,
            "open_rate": round(opened / sent * 100, 1) if sent else None,
            "reply_rate": round(replied / sent * 100, 1) if sent else None,
        })

    return {"stages": stages}


# ---------------------------------------------------------------------------
# Cost Per Meeting
# ---------------------------------------------------------------------------

@router.get("/cost-per-meeting")
def cost_per_meeting(
    user_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = _scope_user_id(current_user, user_id)
    """
    Total costs (Anthropic tokens + subscription costs) / total meetings booked.
    Returns a full breakdown.
    """
    pricing = get_model_pricing(MODEL_SMART)
    inp_rate = pricing["input"]
    out_rate = pricing["output"]

    # Subscription costs
    apollo_monthly = _get_float_setting(db, COST_APOLLO_MONTHLY_KEY, 0.0)
    phantombuster_monthly = _get_float_setting(db, COST_PHANTOMBUSTER_MONTHLY_KEY, 0.0)
    reacher_monthly = _get_float_setting(db, COST_REACHER_MONTHLY_KEY, 0.0)
    hunter_monthly = _get_float_setting(db, COST_HUNTER_MONTHLY_KEY, 0.0)
    proxycurl_monthly = 0.0  # Proxycurl removed (shut down 2025)
    tavily_monthly = _get_float_setting(db, COST_TAVILY_MONTHLY_KEY, 0.0)
    total_subscriptions = (
        apollo_monthly + phantombuster_monthly
        + reacher_monthly + hunter_monthly + proxycurl_monthly + tavily_monthly
    )

    # Token costs
    q = db.query(Campaign)
    if user_id:
        q = q.filter(Campaign.user_id == user_id)
    campaigns = q.all()

    total_inp = sum(c.tokens_input or 0 for c in campaigns)
    total_out = sum(c.tokens_output or 0 for c in campaigns)
    total_anthropic = (total_inp / 1_000_000 * inp_rate) + (total_out / 1_000_000 * out_rate)

    total_cost = total_subscriptions + total_anthropic
    campaign_ids = [c.id for c in campaigns]
    total_meetings = db.query(func.count(Reply.id)).filter(
        Reply.campaign_id.in_(campaign_ids),
        Reply.status == ReplyStatus.meeting_booked,
    ).scalar() or 0 if campaign_ids else 0

    return {
        "total_meetings_booked": total_meetings,
        "total_cost_usd": round(total_cost, 2),
        "cost_per_meeting_usd": round(total_cost / total_meetings, 2) if total_meetings else None,
        "breakdown": {
            "anthropic_cost_usd": round(total_anthropic, 4),
            "subscription_cost_usd": round(total_subscriptions, 2),
            "subscriptions": {
                "apollo": apollo_monthly,
                "phantombuster": phantombuster_monthly,
                "reacher": reacher_monthly,
                "hunter": hunter_monthly,
                "proxycurl": proxycurl_monthly,
                "tavily": tavily_monthly,
            },
        },
    }


# ---------------------------------------------------------------------------
# Channel Attribution
# ---------------------------------------------------------------------------

@router.get("/channel-attribution")
def channel_attribution(
    campaign_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    For replies that came in: which was the last email step sent before the reply?
    Groups by step number to show which step drives responses.
    """
    # Get sequences that have replies
    seq_q = db.query(EmailSequence).filter(EmailSequence.replied_at.isnot(None))
    if campaign_id:
        seq_q = seq_q.filter(EmailSequence.campaign_id == campaign_id)
    replied_sequences = seq_q.all()

    if not replied_sequences:
        return {"total_replies": 0, "attribution": []}

    # For each replied sequence, find the last send log step before the reply
    attribution: dict[int, int] = defaultdict(int)

    for seq in replied_sequences:
        # Find the last send log for this sequence before the reply
        last_send = (
            db.query(SendLog)
            .filter(
                SendLog.sequence_id == seq.id,
                SendLog.sent_at <= seq.replied_at if seq.replied_at else True,
            )
            .order_by(SendLog.step.desc())
            .first()
        )
        if last_send:
            attribution[last_send.step] += 1
        else:
            # Fall back to current_step on the sequence.
            # current_step = "next step to send", so last sent = current_step - 1.
            attribution[max(1, (seq.current_step or 1) - 1)] += 1

    STEP_LABELS = {1: "Email 1", 2: "Email 2", 3: "Email 3", 4: "Email 4",
                   5: "Email 5", 6: "Email 6", 7: "LinkedIn"}

    total_replies = sum(attribution.values())
    results = []
    for step_num in sorted(attribution.keys()):
        count = attribution[step_num]
        results.append({
            "step": step_num,
            "label": STEP_LABELS.get(step_num, f"Step {step_num}"),
            "replies": count,
            "share_pct": round(count / total_replies * 100, 1) if total_replies else 0,
        })

    return {
        "total_replies": total_replies,
        "attribution": results,
    }
