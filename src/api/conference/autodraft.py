"""
Conference auto-draft sweep (Events-auto-draft 2026-04-10).

Once a rep clicks "Push to Drafts" on email 1 for a conference lead, every
subsequent step in the generated sequence should land in their Outlook Drafts
folder automatically on the day that step's ``day_offset`` comes due  -  unless
the lead has replied in the meantime.

This module is the sweep that delivers that promise. It is invoked once per
user per scheduler tick from ``main.py:_scheduler_loop``. It is intentionally
stateless: every decision is derived from the current DB state of each
ConferenceLead row (``draft_created_at``, ``replied_at``,
``generated_sequence._drafted_steps``) plus the real wall-clock.

Due condition (per step)
------------------------
    anchor_local  = lead.draft_created_at.astimezone(local_tz)
    target_date   = anchor_local.date() + timedelta(days=step.day_offset)
    now_local     = now_utc.astimezone(local_tz)

    due =  now_local.date() > target_date
           or (now_local.date() == target_date and now_local.hour >= 9)

The 09:00 local-time gate matches the anchor used by
``schedule_followup_reminders`` so an auto-draft lands at the exact moment
the matching calendar popup would have fired. The local timezone is whatever
``CONFERENCE_REMINDER_TIMEZONE`` is set to (default ``Asia/Dubai``).

Reply cutoff
------------
Leads with ``replied_at IS NOT NULL`` are excluded at the query level  -  not a
single draft goes out for a replied lead. This mirrors the hard 409 the manual
route raises via ``_assert_not_replied``.

Failure isolation
-----------------
- One try/except per lead so a single bad row (e.g. disconnected Outlook) does
  not abort the whole sweep.
- One try/except per step inside a lead so one bad step key does not block the
  remaining steps on the same lead.
- DB commit per successful lead keeps the blast radius small if the process
  dies mid-sweep.
- ``cancel_followup_reminders`` is called best-effort for each auto-drafted
  step so the rep does not also receive the stale calendar popup.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...db.models import ConferenceLead, User
from .service import (
    _load_seq_steps,
    _reminder_timezone,
    cancel_followup_reminders,
    draft_one_step,
)

logger = logging.getLogger(__name__)


@dataclass
class AutodraftSummary:
    leads_considered: int = 0
    steps_drafted: int = 0
    steps_failed: int = 0
    leads_with_errors: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def bump(self, key: str) -> None:
        self.reasons[key] = self.reasons.get(key, 0) + 1

    def as_dict(self) -> dict:
        return {
            "leads_considered": self.leads_considered,
            "steps_drafted": self.steps_drafted,
            "steps_failed": self.steps_failed,
            "leads_with_errors": self.leads_with_errors,
            "reasons": dict(self.reasons),
        }


def _local_tz():
    """Return the rep's local timezone (mirrors ``_reminder_timezone``)."""
    tz_name = _reminder_timezone()
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _step_is_due(
    draft_created_at: datetime,
    day_offset: int,
    now_utc: datetime,
    local_tz,
    *,
    gate_hour: int = 9,
) -> bool:
    """Return True when the given step_offset day has arrived in the rep's
    local timezone and the 09:00 gate has passed.

    Pure function  -  no DB, no side effects. Fully unit-testable.
    """
    if draft_created_at.tzinfo is None:
        draft_created_at = draft_created_at.replace(tzinfo=timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    anchor_local = draft_created_at.astimezone(local_tz)
    now_local = now_utc.astimezone(local_tz)
    target_date = anchor_local.date() + timedelta(days=int(day_offset))

    if now_local.date() > target_date:
        return True
    if now_local.date() == target_date and now_local.hour >= gate_hour:
        return True
    return False


def _coerce_datetime(val) -> "datetime | None":
    """Normalise the SQL driver's idea of a DATETIME column to a Python
    ``datetime`` object. Postgres' psycopg returns real datetimes; SQLite
    (used in tests) returns ISO-ish strings. Raw SQL via ``text()`` skips
    the ORM layer's automatic conversion, so we do it here.
    """
    if val is None or isinstance(val, datetime):
        return val
    if isinstance(val, str):
        # SQLite format: "2026-04-01 10:00:00.000000" or with "T" separator
        s = val.strip().replace("T", " ")
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S%z",
        ):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


def _read_current_lead_state(db: Session, lead_id: str) -> "tuple[dict | None, datetime | None, datetime | None, str]":
    """Raw SQL fetch of the columns we need for a concurrency-safe step
    decision. Returns ``(seq_dict_or_none, replied_at, draft_created_at,
    reason)`` where ``reason`` is a sentinel string for summary bookkeeping
    when the sequence can't be parsed. Both datetimes are coerced from
    the SQL driver's native representation to real ``datetime`` objects.
    """
    row = db.execute(
        text(
            "SELECT generated_sequence, replied_at, draft_created_at "
            "FROM conference_leads WHERE id = :id"
        ),
        {"id": lead_id},
    ).first()
    if row is None:
        return None, None, None, "missing"
    raw_seq, replied_at_raw, draft_created_at_raw = row
    replied_at = _coerce_datetime(replied_at_raw)
    draft_created_at = _coerce_datetime(draft_created_at_raw)
    if not raw_seq:
        return None, replied_at, draft_created_at, "no_sequence"
    try:
        seq = json.loads(raw_seq) if isinstance(raw_seq, str) else raw_seq
    except (json.JSONDecodeError, TypeError):
        return None, replied_at, draft_created_at, "corrupt_sequence"
    if not isinstance(seq, dict):
        return None, replied_at, draft_created_at, "non_dict_sequence"
    return seq, replied_at, draft_created_at, "ok"


def _atomic_mark_step_drafted(db: Session, lead_id: str, step_key: str) -> bool:
    """Read-merge-write the ``_drafted_steps`` array atomically so a
    concurrent write from another session isn't clobbered. Commits on
    success. Returns True if the step was newly marked; False if it was
    already present (concurrent process won the race) or if the row
    vanished. Never raises.
    """
    try:
        row = db.execute(
            text("SELECT generated_sequence FROM conference_leads WHERE id = :id"),
            {"id": lead_id},
        ).first()
        if row is None or not row[0]:
            return False
        raw = row[0]
        seq = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(seq, dict):
            return False
        drafted = list(seq.get("_drafted_steps") or [])
        if step_key in drafted:
            db.commit()
            return False  # already recorded
        drafted.append(step_key)
        seq["_drafted_steps"] = drafted
        db.execute(
            text(
                "UPDATE conference_leads SET generated_sequence = :seq "
                "WHERE id = :id"
            ),
            {"seq": json.dumps(seq), "id": lead_id},
        )
        db.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Autodraft: atomic mark failed lead=%s step=%s: %s",
            lead_id, step_key, exc, exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return False


def _rollback_step_mark(db: Session, lead_id: str, step_key: str) -> None:
    """SWAT-C2: remove a step_key from _drafted_steps after a failed Graph call."""
    try:
        row = db.execute(
            text("SELECT generated_sequence FROM conference_leads WHERE id = :id"),
            {"id": lead_id},
        ).first()
        if row is None or not row[0]:
            return
        raw = row[0]
        seq = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(seq, dict):
            return
        drafted = list(seq.get("_drafted_steps") or [])
        if step_key in drafted:
            drafted.remove(step_key)
            seq["_drafted_steps"] = drafted
            db.execute(
                text("UPDATE conference_leads SET generated_sequence = :seq WHERE id = :id"),
                {"seq": json.dumps(seq), "id": lead_id},
            )
            db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _process_lead(
    db: Session,
    user: User,
    lead_id: str,
    seq_steps: list,
    now_utc: datetime,
    local_tz,
    gate_hour: int,
    summary: "AutodraftSummary",
    signature_html: str = "",
) -> bool:
    """Process one lead's due steps. Each step is decided against a fresh
    DB read and recorded via an atomic read-merge-write so concurrent
    writers don't collide. Returns True if any step hit an error for
    this lead (used for the ``leads_with_errors`` counter).

    The fresh reads also cover the "lead replied mid-sweep" case  -  if the
    reply poller sets ``replied_at`` after the outer query ran, this
    function stops sending drafts for that lead immediately.
    """
    had_error = False
    for step_num, day_offset, _limit, _desc, *_ in seq_steps:
        if step_num <= 1:
            continue
        step_key = f"email{step_num}"

        seq, replied_at, draft_created_at, reason = _read_current_lead_state(db, lead_id)
        if reason == "missing":
            return had_error  # row deleted mid-sweep; nothing to do
        if reason in ("corrupt_sequence", "non_dict_sequence"):
            summary.bump(reason)
            return had_error
        if reason == "no_sequence":
            return had_error
        if replied_at is not None:
            # Reply arrived since the sweep started  -  stop this lead now.
            return had_error
        if draft_created_at is None:
            return had_error  # clear-draft happened mid-sweep

        drafted_steps = set(seq.get("_drafted_steps") or [])
        if step_key in drafted_steps:
            continue
        if step_key not in seq:
            continue
        if not _step_is_due(
            draft_created_at, day_offset, now_utc, local_tz,
            gate_hour=gate_hour,
        ):
            continue

        # Build a lightweight lead object with the addressing fields the
        # Graph call needs. We use the ORM row only for first_name/last_name/
        # email/cc  -  those never change for a given lead, so staleness is
        # not a concern here.
        lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
        if lead is None:
            return had_error

        # SWAT-C2: atomically claim the step BEFORE calling Graph API.
        # This prevents concurrent autodraft/manual-draft from both sending.
        newly_marked = _atomic_mark_step_drafted(db, lead_id, step_key)
        if not newly_marked:
            continue  # another thread/sweep already claimed it

        try:
            draft_one_step(lead, step_key, user, seq_override=seq, db=db, signature_html=signature_html)
        except ValueError as exc:
            _rollback_step_mark(db, lead_id, step_key)
            logger.warning(
                "Autodraft skip lead=%s step=%s: %s",
                lead.email, step_key, exc,
            )
            summary.bump(f"skip_{exc.__class__.__name__}")
            had_error = True
            continue
        except Exception as exc:  # noqa: BLE001
            _rollback_step_mark(db, lead_id, step_key)
            logger.error(
                "Autodraft failed lead=%s step=%s: %s",
                lead.email, step_key, exc, exc_info=True,
            )
            summary.steps_failed += 1
            had_error = True
            continue

        summary.steps_drafted += 1
        logger.info(
            "Autodraft: drafted %s for lead %s (user=%s)",
            step_key, lead.email, user.email,
        )

        try:
            cancel_followup_reminders(lead, user, step_keys=[step_key])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Autodraft: calendar cancel failed for %s/%s: %s",
                lead.email, step_key, exc,
            )

    return had_error


def run_conference_autodraft_sweep(
    db: Session,
    user: User,
    *,
    now_utc: Optional[datetime] = None,
) -> AutodraftSummary:
    """Drive one sweep of conference auto-drafts for a single user.

    Args:
        db:       SQLAlchemy session.
        user:     The rep we are sweeping. Required  -  the Outlook token path
                  is resolved from ``user``, and the query is user-scoped so
                  one rep's sweep never touches another rep's leads.
        now_utc:  UTC wall-clock to use for due-time decisions. Defaults to
                  ``datetime.now(tz.utc)``; injectable from tests.

    Returns:
        ``AutodraftSummary`` with counters. Never raises  -  all failures are
        logged and rolled into the summary so a flaky step can't take down
        the scheduler loop.
    """
    summary = AutodraftSummary()
    if user is None or not getattr(user, "id", None):
        return summary

    now_utc = now_utc or datetime.now(timezone.utc)
    local_tz = _local_tz()

    # Query: every active conference-sequence lead owned by this user.
    # "Active" means email 1 has been drafted (draft_created_at set), the
    # lead hasn't replied (replied_at null), and a sequence exists to draft
    # from. Anything not matching this set is a no-op for the sweep.
    leads = (
        db.query(ConferenceLead)
        .filter(
            ConferenceLead.user_id == user.id,
            ConferenceLead.draft_created_at.isnot(None),
            ConferenceLead.replied_at.is_(None),
            ConferenceLead.generated_sequence.isnot(None),
        )
        .all()
    )
    summary.leads_considered = len(leads)
    if not leads:
        return summary

    # Load the canonical step list once. day_offset is days from email 1.
    try:
        seq_steps = _load_seq_steps(db)
    except Exception as exc:
        logger.error("Autodraft: failed to load seq steps: %s", exc, exc_info=True)
        return summary

    # If ALL remaining steps reduce to nothing the sweep is still valuable
    # for its own loop (we log zeros), so keep going.

    gate_hour = int(os.getenv("CONFERENCE_AUTODRAFT_GATE_HOUR", "9"))

    # Load the user's Outlook signature once for the whole sweep.
    from ..settings import _get_user_setting, OUTLOOK_SIGNATURE_KEY
    _sig = _get_user_setting(db, OUTLOOK_SIGNATURE_KEY, user.id)

    # Capture just the lead IDs  -  we re-read each lead's generated_sequence
    # fresh at the start of each step (atomic SELECT) to protect against
    # concurrent writes from the manual draft route or an overlapping tick.
    lead_ids = [lead.id for lead in leads]

    for lead_id in lead_ids:
        lead_had_error = False
        try:
            lead_had_error = _process_lead(
                db, user, lead_id, seq_steps, now_utc, local_tz,
                gate_hour, summary, signature_html=_sig,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Autodraft: unexpected error on lead_id=%s: %s",
                lead_id, exc, exc_info=True,
            )
            try:
                db.rollback()
            except Exception:
                pass
            lead_had_error = True

        if lead_had_error:
            summary.leads_with_errors += 1

    if summary.steps_drafted or summary.steps_failed:
        logger.info(
            "Autodraft sweep done user=%s: %s",
            user.email, summary.as_dict(),
        )
    return summary
