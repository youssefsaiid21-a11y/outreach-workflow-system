"""
Unit tests for src/api/conference/autodraft.py (Events-auto-draft 2026-04-10).

Covers the pure due-time predicate and the full sweep against an in-memory
SQLite DB with a mocked Outlook draft backend. No network, no real tokens.

Scenarios:
    1. Step is due (day_offset elapsed, past 09:00 local) → drafted.
    2. Step not yet due (same calendar day, before 09:00) → skipped.
    3. Step not yet due (before target date) → skipped.
    4. Step already drafted (_drafted_steps contains step_key) → skipped.
    5. Lead has replied_at set → all steps skipped (excluded at query level).
    6. Sequence JSON is missing the due step (3-step conf) → skipped.
    7. Backend raises - other leads still drafted (error isolation).
    8. draft_one_step updates _drafted_steps so a second sweep is a no-op.
    9. Multiple due steps in one tick get drafted in the same sweep.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Bootstrap path + env so module imports succeed.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("CONFERENCE_REMINDER_TIMEZONE", "UTC")  # deterministic tests

from src.db.models import Base, ConferenceLead, User  # noqa: E402
from src.api.conference import autodraft as ad  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def user(db):
    u = User(
        id="u-nehal",
        email="nehal@example.com",
        name="Nehal",
        password_hash="x",
    )
    db.add(u)
    db.commit()
    return u


def _default_sequence() -> dict:
    """Match the 6-step _DEFAULT_SEQ_STEPS so every step key exists."""
    return {
        "email1": {"subject": "Nice to meet you", "body": "Hey {{first_name}}, great chat."},
        "email2": {"subject": "Quick idea", "body": "Thought you'd like this insight."},
        "email3": {"subject": "One case study", "body": "Relevant to your industry."},
        "email4": {"subject": "Different angle", "body": "Quick question for you."},
        "email5": {"subject": "Checking in", "body": "Low-pressure nudge."},
        "email6": {"subject": "Signing off", "body": "Last note from me."},
        "linkedin_connect_note": "",
        "linkedin_message": "",
    }


def _make_lead(
    db,
    user,
    *,
    draft_created_at: datetime,
    replied_at=None,
    drafted_steps=None,
    sequence=None,
    lead_id: str = "L1",
    email: str = "target@corp.com",
) -> ConferenceLead:
    seq = sequence if sequence is not None else _default_sequence()
    if drafted_steps is not None:
        seq["_drafted_steps"] = list(drafted_steps)
    lead = ConferenceLead(
        id=lead_id,
        conference_name="ADIPEC",
        first_name="Alex",
        last_name="Example",
        email=email,
        user_id=user.id,
        generated_subject="",
        generated_body="",
        generated_sequence=json.dumps(seq),
        draft_message_id="msg-1",
        draft_created_at=draft_created_at,
        replied_at=replied_at,
    )
    db.add(lead)
    db.commit()
    return lead


# ── Due-time predicate ─────────────────────────────────────────────────────


class TestStepIsDue:
    def test_day_elapsed_past_gate(self):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)  # anchor + 3d, 09:30
        assert ad._step_is_due(anchor, 3, now, timezone.utc) is True

    def test_day_elapsed_before_gate(self):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 8, 59, tzinfo=timezone.utc)  # anchor + 3d, before 09:00
        assert ad._step_is_due(anchor, 3, now, timezone.utc) is False

    def test_day_not_yet_reached(self):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 3, 23, 0, tzinfo=timezone.utc)  # day 2 of 3
        assert ad._step_is_due(anchor, 3, now, timezone.utc) is False

    def test_day_far_past(self):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 20, 4, 0, tzinfo=timezone.utc)  # way past, before 09
        # day has elapsed, strictly greater, so gate doesn't matter
        assert ad._step_is_due(anchor, 3, now, timezone.utc) is True

    def test_naive_draft_created_at_treated_as_utc(self):
        anchor_naive = datetime(2026, 4, 1, 10, 0)  # no tz
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        assert ad._step_is_due(anchor_naive, 3, now, timezone.utc) is True

    def test_custom_gate_hour(self):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 6, 30, tzinfo=timezone.utc)  # 06:30 local
        assert ad._step_is_due(anchor, 3, now, timezone.utc, gate_hour=6) is True
        assert ad._step_is_due(anchor, 3, now, timezone.utc, gate_hour=9) is False


# ── End-to-end sweep with mocked draft backend ─────────────────────────────


class _FakeDraftCalls:
    """Captures every (email, subject, body) tuple that would hit Outlook."""

    def __init__(self):
        self.calls = []
        self.raise_for_emails = set()  # set of lead.email to fail on

    def create_draft_outlook(self, to_email, to_name, subject, body, token_file=None, cc=None, signature_html=""):
        if to_email in self.raise_for_emails:
            raise RuntimeError("Graph 500")
        self.calls.append({
            "to": to_email, "to_name": to_name,
            "subject": subject, "body": body, "cc": cc,
        })
        return {"message_id": f"mid-{len(self.calls)}"}


@pytest.fixture
def mock_draft_backend(tmp_path):
    """Patches both the Outlook token check and the draft creation so the
    sweep runs end-to-end without touching MSAL or Graph."""
    fake = _FakeDraftCalls()

    # Pretend the Outlook token file exists (pass the "is user connected" check).
    token_file = tmp_path / "token_cache_u-nehal.json"
    token_file.write_text("{}")

    # draft_one_step imports `draft_svc` + `_outlook_token_file` at module
    # load time in service.py - patch at that site.
    with patch("src.api.conference.service._outlook_token_file", return_value=token_file), \
         patch("src.api.conference.service.draft_svc.create_draft_outlook",
               side_effect=fake.create_draft_outlook), \
         patch("src.api.conference.service.cancel_followup_reminders", return_value=None), \
         patch("src.api.conference.autodraft.cancel_followup_reminders", return_value=None):
        yield fake


# ── Scenario 1: due step gets drafted ──────────────────────────────────────


def test_due_step_drafted(db, user, mock_draft_backend):
    anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)  # day 3, past 09
    lead = _make_lead(db, user, draft_created_at=anchor, drafted_steps=["email1"])

    summary = ad.run_conference_autodraft_sweep(db, user, now_utc=now)

    assert summary.steps_drafted == 1
    assert summary.steps_failed == 0
    assert len(mock_draft_backend.calls) == 1
    assert mock_draft_backend.calls[0]["subject"] == "Quick idea"

    # Persist and verify _drafted_steps updated
    db.refresh(lead)
    seq = json.loads(lead.generated_sequence)
    assert "email2" in seq["_drafted_steps"]
    # draft_created_at must NOT be bumped (that's email 1's anchor forever)
    assert lead.draft_created_at.replace(tzinfo=timezone.utc) == anchor


# ── Scenario 2: before 09:00 local → skipped ───────────────────────────────


def test_before_gate_skipped(db, user, mock_draft_backend):
    anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 4, 8, 59, tzinfo=timezone.utc)  # day 3, 08:59
    _make_lead(db, user, draft_created_at=anchor, drafted_steps=["email1"])

    summary = ad.run_conference_autodraft_sweep(db, user, now_utc=now)

    assert summary.steps_drafted == 0
    assert len(mock_draft_backend.calls) == 0


# ── Scenario 3: day not reached ─────────────────────────────────────────────


def test_day_not_reached(db, user, mock_draft_backend):
    anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 2, 23, 59, tzinfo=timezone.utc)  # day 1, late
    _make_lead(db, user, draft_created_at=anchor, drafted_steps=["email1"])

    summary = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
    assert summary.steps_drafted == 0


# ── Scenario 4: already drafted ────────────────────────────────────────────


def test_already_drafted_not_redrafted(db, user, mock_draft_backend):
    anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
    _make_lead(
        db, user, draft_created_at=anchor,
        drafted_steps=["email1", "email2"],  # already done
    )

    summary = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
    assert summary.steps_drafted == 0
    assert len(mock_draft_backend.calls) == 0


# ── Scenario 5: replied_at set → excluded ──────────────────────────────────


def test_replied_lead_excluded(db, user, mock_draft_backend):
    anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 10, 9, 30, tzinfo=timezone.utc)  # many days later
    _make_lead(
        db, user,
        draft_created_at=anchor,
        replied_at=datetime(2026, 4, 2, tzinfo=timezone.utc),
        drafted_steps=["email1"],
    )

    summary = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
    assert summary.leads_considered == 0  # query excludes at DB level
    assert summary.steps_drafted == 0
    assert len(mock_draft_backend.calls) == 0


# ── Scenario 6: sequence shorter than default (3-step conf) ────────────────


def test_short_sequence_skips_missing_steps(db, user, mock_draft_backend):
    anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    # Many days past so all steps that exist would be "due"
    now = datetime(2026, 5, 1, 9, 30, tzinfo=timezone.utc)

    # 3-step sequence - no email4/5/6 present
    short = {
        "email1": {"subject": "Hi", "body": "Nice to meet."},
        "email2": {"subject": "Idea", "body": "Follow up insight."},
        "email3": {"subject": "Case", "body": "Quick story."},
    }
    _make_lead(
        db, user, draft_created_at=anchor,
        drafted_steps=["email1"], sequence=short,
    )

    summary = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
    # email2 + email3 drafted, email4-6 skipped silently
    assert summary.steps_drafted == 2
    drafted_subjects = [c["subject"] for c in mock_draft_backend.calls]
    assert "Idea" in drafted_subjects
    assert "Case" in drafted_subjects


# ── Scenario 7: one lead raises, others keep going ─────────────────────────


def test_error_isolation(db, user, mock_draft_backend):
    anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)

    _make_lead(db, user, draft_created_at=anchor, drafted_steps=["email1"],
               lead_id="L-boom", email="boom@corp.com")
    _make_lead(db, user, draft_created_at=anchor, drafted_steps=["email1"],
               lead_id="L-ok", email="ok@corp.com")

    mock_draft_backend.raise_for_emails.add("boom@corp.com")

    summary = ad.run_conference_autodraft_sweep(db, user, now_utc=now)

    assert summary.steps_drafted == 1
    assert summary.steps_failed == 1
    assert summary.leads_with_errors == 1
    # The working lead got drafted
    ok = [c for c in mock_draft_backend.calls if c["to"] == "ok@corp.com"]
    assert len(ok) == 1


# ── Scenario 8: second sweep is a no-op ────────────────────────────────────


def test_second_sweep_idempotent(db, user, mock_draft_backend):
    anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
    _make_lead(db, user, draft_created_at=anchor, drafted_steps=["email1"])

    s1 = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
    s2 = ad.run_conference_autodraft_sweep(db, user, now_utc=now)

    assert s1.steps_drafted == 1
    assert s2.steps_drafted == 0
    assert len(mock_draft_backend.calls) == 1


# ── Scenario 9: multiple due steps in one tick ─────────────────────────────


def test_multiple_steps_due_same_tick(db, user, mock_draft_backend):
    # _DEFAULT_SEQ_STEPS offsets are 0,3,7,14,21,28
    # At anchor+8d 09:30, email2 (d3) and email3 (d7) are both due.
    anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 9, 9, 30, tzinfo=timezone.utc)  # +8 days
    _make_lead(db, user, draft_created_at=anchor, drafted_steps=["email1"])

    summary = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
    assert summary.steps_drafted == 2
    subjects = sorted(c["subject"] for c in mock_draft_backend.calls)
    assert subjects == ["One case study", "Quick idea"]


# ── Scenario 10: cross-user isolation ──────────────────────────────────────


def test_cross_user_isolation(db, user, mock_draft_backend):
    # Second user with their own lead - must NOT be swept when we call for
    # the first user. Ensures the query is strictly user-scoped.
    other = User(id="u-selma", email="selma@example.com", name="Selma", password_hash="x")
    db.add(other)
    db.commit()

    anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)

    _make_lead(db, user, draft_created_at=anchor, drafted_steps=["email1"],
               lead_id="L-nehal", email="nehal-lead@corp.com")
    # Selma's lead - same due conditions
    selma_lead = ConferenceLead(
        id="L-selma",
        conference_name="ADIPEC",
        first_name="Other",
        last_name="Contact",
        email="selma-lead@corp.com",
        user_id=other.id,
        generated_sequence=json.dumps({**_default_sequence(), "_drafted_steps": ["email1"]}),
        draft_message_id="msg-s",
        draft_created_at=anchor,
    )
    db.add(selma_lead)
    db.commit()

    summary = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
    # Only Nehal's lead drafted
    assert summary.leads_considered == 1
    assert summary.steps_drafted == 1
    drafted_to = [c["to"] for c in mock_draft_backend.calls]
    assert drafted_to == ["nehal-lead@corp.com"]
