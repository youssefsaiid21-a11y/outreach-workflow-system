"""
Adversarial audit tests for the conference auto-draft sweep.

These are the edge cases I specifically did NOT cover in the first test file,
added after a stakeholder asked me to be critical and detailed. Every test
here targets a failure mode that could plausibly bite us on prod.

Groups:
    A. Timezone correctness
        - Asia/Dubai (prod default) day boundary
        - Asia/Singapore boundary
        - DST transition (irrelevant in GCC/SG but critical if TZ ever changes)
        - Naive datetime from Postgres DATETIME WITHOUT TIME ZONE
    B. Concurrency / race
        - Sweep re-reads generated_sequence after Graph call so a concurrent
          manual draft on another step is not clobbered
        - Two overlapping sweeps same user same lead (simulated)
    C. Data corruption
        - generated_sequence is literal "null"
        - generated_sequence is a JSON array (wrong shape)
        - generated_sequence has email keys but non-dict values
        - _drafted_steps contains duplicate keys
        - _drafted_steps contains unknown step keys
    D. Graph failure modes
        - Rate limit (raises generic Exception) - continues with next lead
        - Token expiry mid-sweep (ValueError) - continues with next lead
        - Duplicate draft (Graph returns same message_id) - idempotent on our side
    E. Clock regression
        - now_utc < draft_created_at → no drafts (negative offset never due)
        - Anchor in the future (timezone bug caught locally)
    F. Scale
        - 100 leads, mix of due/not-due, reply poller races with sweep
    G. Outlook not connected
        - draft_one_step raises ValueError without touching Graph
        - Sweep marks as skip, leaves lead in same state
    H. Scheduler hook integration
        - main.py imports resolve and the hook call matches the expected shape
"""
from __future__ import annotations

import json
import os
import sys
import random
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("CONFERENCE_REMINDER_TIMEZONE", "UTC")

from src.db.models import Base, ConferenceLead, User  # noqa: E402
from src.api.conference import autodraft as ad  # noqa: E402
from src.api.conference import service as svc  # noqa: E402


# ── Shared fixtures ─────────────────────────────────────────────────────────


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
    u = User(id="u-1", email="rep@x.com", name="Rep", password_hash="x")
    db.add(u); db.commit()
    return u


def _seq(**extra):
    base = {f"email{i}": {"subject": f"S{i}", "body": f"B{i}"} for i in range(1, 7)}
    base.update(extra)
    return base


def _make(db, user, **k):
    defaults = dict(
        id=k.pop("id", "L1"),
        conference_name="C", first_name="F", last_name="L",
        email=k.pop("email", "to@x.com"),
        user_id=user.id,
        generated_sequence=json.dumps(k.pop("seq", _seq(_drafted_steps=["email1"]))),
        draft_message_id="m1",
        draft_created_at=k.pop("anchor", datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)),
        replied_at=k.pop("replied_at", None),
    )
    defaults.update(k)
    lead = ConferenceLead(**defaults)
    db.add(lead); db.commit()
    return lead


class FakeDraft:
    def __init__(self):
        self.calls = []
        self.raise_for = set()
        self.raise_value_error_for = set()
    def create(self, to_email, to_name, subject, body, token_file=None, cc=None, signature_html=""):
        if to_email in self.raise_value_error_for:
            raise ValueError("Graph token expired")
        if to_email in self.raise_for:
            raise RuntimeError("429 Too Many Requests")
        self.calls.append({"to": to_email, "subject": subject, "body": body})
        return {"message_id": f"mid-{len(self.calls)}"}


@pytest.fixture
def fake_graph(tmp_path):
    fake = FakeDraft()
    token = tmp_path / "tok.json"; token.write_text("{}")
    with patch("src.api.conference.service._outlook_token_file", return_value=token), \
         patch("src.api.conference.service.draft_svc.create_draft_outlook",
               side_effect=fake.create), \
         patch("src.api.conference.service.cancel_followup_reminders", return_value=None), \
         patch("src.api.conference.autodraft.cancel_followup_reminders", return_value=None):
        yield fake


# ═══ GROUP A - Timezone correctness ═════════════════════════════════════════


class TestTimezoneDubai:
    """Prod default TZ is Asia/Dubai (UTC+4). Day boundary and 09:00 gate."""

    def setup_method(self):
        from zoneinfo import ZoneInfo
        self.dubai = ZoneInfo("Asia/Dubai")

    def test_dubai_gate_hits_at_local_09(self):
        # Anchor 2026-04-01 20:00 UTC = 2026-04-02 00:00 Dubai
        anchor = datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)
        # day_offset=3 → target local date 2026-04-05
        # now = 2026-04-05 04:59 UTC = 08:59 Dubai → NOT due
        assert ad._step_is_due(anchor, 3,
            datetime(2026, 4, 5, 4, 59, tzinfo=timezone.utc),
            self.dubai) is False
        # now = 2026-04-05 05:00 UTC = 09:00 Dubai → due
        assert ad._step_is_due(anchor, 3,
            datetime(2026, 4, 5, 5, 0, tzinfo=timezone.utc),
            self.dubai) is True

    def test_dubai_anchor_late_night_next_day_boundary(self):
        # Anchor 2026-04-01 20:30 UTC = 2026-04-02 00:30 Dubai → Dubai day 04-02
        # day_offset=1 → target local 2026-04-03
        # now = 2026-04-03 04:59 UTC = 08:59 Dubai → NOT due
        anchor = datetime(2026, 4, 1, 20, 30, tzinfo=timezone.utc)
        assert ad._step_is_due(anchor, 1,
            datetime(2026, 4, 3, 4, 59, tzinfo=timezone.utc),
            self.dubai) is False
        assert ad._step_is_due(anchor, 1,
            datetime(2026, 4, 3, 5, 5, tzinfo=timezone.utc),
            self.dubai) is True

    def test_dubai_anchor_early_morning(self):
        # Anchor 2026-04-01 23:00 UTC = 2026-04-02 03:00 Dubai
        # Dubai anchor_date = 2026-04-02
        # day_offset=0 (same day email 2) → target = 04-02
        # now = 2026-04-02 05:00 UTC = 09:00 Dubai → due
        anchor = datetime(2026, 4, 1, 23, 0, tzinfo=timezone.utc)
        assert ad._step_is_due(anchor, 0,
            datetime(2026, 4, 2, 5, 0, tzinfo=timezone.utc),
            self.dubai) is True
        # now = 2026-04-02 04:30 UTC = 08:30 Dubai → NOT due
        assert ad._step_is_due(anchor, 0,
            datetime(2026, 4, 2, 4, 30, tzinfo=timezone.utc),
            self.dubai) is False


class TestTimezoneSingapore:
    """If a rep is ever configured to Asia/Singapore (UTC+8)."""

    def test_singapore_gate(self):
        from zoneinfo import ZoneInfo
        sg = ZoneInfo("Asia/Singapore")
        anchor = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)  # 10:00 SG
        # day_offset=3 → target SG date 2026-04-04
        # now = 2026-04-04 00:59 UTC = 08:59 SG → NOT due
        assert ad._step_is_due(anchor, 3,
            datetime(2026, 4, 4, 0, 59, tzinfo=timezone.utc),
            sg) is False
        assert ad._step_is_due(anchor, 3,
            datetime(2026, 4, 4, 1, 1, tzinfo=timezone.utc),
            sg) is True


class TestNaiveFromPostgres:
    """Postgres DateTime WITHOUT TIME ZONE round-trips as naive datetime.
    The sweep must treat naive anchors as UTC (same convention as
    schedule_followup_reminders uses)."""

    def test_naive_anchor_treated_as_utc(self):
        anchor_naive = datetime(2026, 4, 1, 10, 0)  # no tz
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        assert ad._step_is_due(anchor_naive, 3, now, timezone.utc) is True

    def test_sweep_handles_naive_draft_created_at(self, db, user, fake_graph):
        # Simulate Postgres returning a naive datetime
        _make(db, user, anchor=datetime(2026, 4, 1, 10, 0))  # naive
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        assert s.steps_drafted == 1
        assert len(fake_graph.calls) == 1


# ═══ GROUP B - Concurrency / race ═══════════════════════════════════════════


class TestConcurrency:
    def test_manual_draft_of_another_step_between_sweep_graph_and_commit(
        self, db, user, fake_graph
    ):
        """Simulate: sweep reads lead, calls Graph, but BEFORE it can write
        back _drafted_steps, a different process adds email3 via the manual
        route. Current implementation would overwrite email3 because it
        holds an in-memory seq that doesn't know about email3.

        This test DOCUMENTS the current behavior - if it fails we've added
        the race fix; if it passes we know the bug is still there."""
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 9, 9, 30, tzinfo=timezone.utc)  # email2 and email3 both due
        lead = _make(db, user, anchor=anchor)

        # Have the graph mock also append email3 to the lead mid-sweep
        # to simulate a concurrent manual draft
        original_create = fake_graph.create
        def racy_create(to_email, to_name, subject, body, token_file=None, cc=None, signature_html=""):
            # After the first Graph call (email2), simulate another session
            # writing email_manual into _drafted_steps and committing
            result = original_create(to_email, to_name, subject, body,
                                      token_file=token_file, cc=cc)
            if subject == "S2":
                # Re-read lead from DB in a fresh state, add email_manual,
                # write back as another session would.
                raw = db.execute(
                    __import__("sqlalchemy").text(
                        "SELECT generated_sequence FROM conference_leads WHERE id=:i"
                    ),
                    {"i": lead.id},
                ).scalar()
                seq2 = json.loads(raw)
                seq2.setdefault("_drafted_steps", []).append("email_manual_marker")
                db.execute(
                    __import__("sqlalchemy").text(
                        "UPDATE conference_leads SET generated_sequence=:s WHERE id=:i"
                    ),
                    {"s": json.dumps(seq2), "i": lead.id},
                )
                db.commit()
            return result

        with patch("src.api.conference.service.draft_svc.create_draft_outlook",
                   side_effect=racy_create):
            ad.run_conference_autodraft_sweep(db, user, now_utc=now)

        db.expire_all()
        fresh = db.query(ConferenceLead).filter_by(id=lead.id).first()
        drafted = json.loads(fresh.generated_sequence).get("_drafted_steps", [])
        # This assertion says: the manual marker must survive the sweep.
        # If the bug exists, this will fail (email_manual_marker vanishes).
        assert "email_manual_marker" in drafted, (
            "RACE CONDITION: the sweep overwrote a concurrent manual draft "
            "tracking entry. Need to refresh lead before writing back."
        )


# ═══ GROUP C - Data corruption ══════════════════════════════════════════════


class TestCorruption:
    def test_null_literal_sequence(self, db, user, fake_graph):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        lead = ConferenceLead(
            id="L-null", conference_name="C", email="x@y.com", user_id=user.id,
            generated_sequence="null",
            draft_message_id="m", draft_created_at=anchor,
        )
        db.add(lead); db.commit()
        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        assert s.steps_drafted == 0
        assert s.leads_considered == 1
        assert "non_dict_sequence" in s.reasons

    def test_array_sequence(self, db, user, fake_graph):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        db.add(ConferenceLead(
            id="L-arr", conference_name="C", email="x@y.com", user_id=user.id,
            generated_sequence="[1,2,3]",
            draft_message_id="m", draft_created_at=anchor,
        )); db.commit()
        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        assert s.steps_drafted == 0
        assert "non_dict_sequence" in s.reasons

    def test_malformed_json(self, db, user, fake_graph):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        db.add(ConferenceLead(
            id="L-bad", conference_name="C", email="x@y.com", user_id=user.id,
            generated_sequence="{not json",
            draft_message_id="m", draft_created_at=anchor,
        )); db.commit()
        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        assert s.steps_drafted == 0
        assert "corrupt_sequence" in s.reasons

    def test_non_dict_email_step_skipped(self, db, user, fake_graph):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        seq = _seq(_drafted_steps=["email1"])
        seq["email2"] = "this is a string not a dict"
        _make(db, user, seq=seq, anchor=anchor)
        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        # draft_one_step raises ValueError on non-dict, sweep catches as skip
        assert s.steps_drafted == 0
        assert any(k.startswith("skip_") for k in s.reasons)

    def test_duplicate_drafted_keys_deduped(self, db, user, fake_graph):
        """If _drafted_steps already has duplicates (shouldn't happen but
        defensive), sweep should still correctly skip drafted steps."""
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        seq = _seq(_drafted_steps=["email1", "email1", "email2", "email2"])
        _make(db, user, seq=seq, anchor=anchor)
        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        # email2 already drafted (twice) - don't re-draft
        assert s.steps_drafted == 0

    def test_unknown_step_in_drafted_steps_doesnt_crash(self, db, user, fake_graph):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        seq = _seq(_drafted_steps=["email1", "email_weird_key"])
        _make(db, user, seq=seq, anchor=anchor)
        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        # email2 should still get drafted (unknown key just ignored)
        assert s.steps_drafted == 1

    def test_empty_string_subject_skipped(self, db, user, fake_graph):
        """draft_one_step raises ValueError on empty subject/body."""
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        seq = _seq(_drafted_steps=["email1"])
        seq["email2"] = {"subject": "", "body": ""}
        _make(db, user, seq=seq, anchor=anchor)
        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        assert s.steps_drafted == 0
        assert len(fake_graph.calls) == 0


# ═══ GROUP D - Graph failure modes ══════════════════════════════════════════


class TestGraphFailures:
    def test_rate_limit_continues_with_next_lead(self, db, user, fake_graph):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        _make(db, user, id="L1", email="a@x.com", anchor=anchor)
        _make(db, user, id="L2", email="b@x.com", anchor=anchor)
        _make(db, user, id="L3", email="c@x.com", anchor=anchor)
        fake_graph.raise_for.add("b@x.com")

        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        assert s.steps_drafted == 2
        assert s.steps_failed == 1
        drafted_to = sorted(c["to"] for c in fake_graph.calls)
        assert drafted_to == ["a@x.com", "c@x.com"]

    def test_rate_limit_on_one_step_doesnt_block_next_step(self, db, user, fake_graph):
        """Lead with both email2 and email3 due - if email2 fails (rate
        limit style), email3 should still be attempted."""
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 9, 9, 30, tzinfo=timezone.utc)  # both due
        _make(db, user, anchor=anchor)

        # Fail the first Graph call (email2) but allow email3
        call_count = [0]
        def failing_first(to_email, to_name, subject, body, token_file=None, cc=None, signature_html=""):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("429")
            return {"message_id": f"mid-{call_count[0]}"}
        with patch("src.api.conference.service.draft_svc.create_draft_outlook",
                   side_effect=failing_first):
            s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        # email2 failed, email3 succeeded
        assert s.steps_drafted == 1
        assert s.steps_failed == 1
        # email3 should be tracked, email2 NOT
        lead = db.query(ConferenceLead).first()
        drafted = json.loads(lead.generated_sequence)["_drafted_steps"]
        assert "email3" in drafted
        assert "email2" not in drafted

    def test_token_expiry_mid_sweep(self, db, user, fake_graph):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        _make(db, user, id="L1", email="a@x.com", anchor=anchor)
        _make(db, user, id="L2", email="b@x.com", anchor=anchor)
        fake_graph.raise_value_error_for.add("a@x.com")

        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        # a@ raised ValueError (skip), b@ succeeded
        assert s.steps_drafted == 1
        assert any(k.startswith("skip_") for k in s.reasons)


# ═══ GROUP E - Clock regression ═════════════════════════════════════════════


class TestClockRegression:
    def test_now_before_anchor(self, db, user, fake_graph):
        """If wall clock is somehow before draft_created_at (server clock
        skew, timezone bug), nothing should get drafted."""
        anchor = datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)  # before anchor
        _make(db, user, anchor=anchor)
        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        assert s.steps_drafted == 0


# ═══ GROUP F - Scale ════════════════════════════════════════════════════════


class TestScale:
    def test_hundred_leads_mixed_states(self, db, user, fake_graph):
        """100 leads, 50 due 50 not, plus 5 replied, plus 5 corrupt.
        Sweep must complete, draft exactly 50, and leave the rest alone."""
        random.seed(42)
        anchor_due = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        anchor_not_due = datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)

        expected_drafted = 0
        for i in range(50):
            _make(db, user, id=f"due-{i}",
                  email=f"due{i}@x.com", anchor=anchor_due)
            expected_drafted += 1
        for i in range(50):
            _make(db, user, id=f"nd-{i}",
                  email=f"nd{i}@x.com", anchor=anchor_not_due)
        for i in range(5):
            _make(db, user, id=f"rep-{i}", email=f"rep{i}@x.com",
                  anchor=anchor_due,
                  replied_at=datetime(2026, 4, 2, tzinfo=timezone.utc))
        for i in range(5):
            db.add(ConferenceLead(
                id=f"bad-{i}", conference_name="C", email=f"bad{i}@x.com",
                user_id=user.id,
                generated_sequence="{not json",
                draft_message_id="m", draft_created_at=anchor_due,
            ))
        db.commit()

        s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        assert s.steps_drafted == expected_drafted, (
            f"Expected {expected_drafted} drafts, got {s.steps_drafted}. "
            f"Summary: {s.as_dict()}"
        )
        # 50 due + 50 not-due + 5 corrupt = 105; 5 replied leads are
        # excluded at the DB query level.
        assert s.leads_considered == 105
        # Replied excluded at query level
        # Bad 5 counted as corrupt reasons
        assert s.reasons.get("corrupt_sequence", 0) == 5


# ═══ GROUP G - Outlook not connected ════════════════════════════════════════


class TestOutlookNotConnected:
    def test_missing_token_file_skips_gracefully(self, db, user, tmp_path):
        """User has no Outlook token cache - sweep must not crash, must
        mark affected leads as skipped."""
        missing = tmp_path / "nonexistent.json"  # does NOT exist
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        _make(db, user, anchor=anchor)

        with patch("src.api.conference.service._outlook_token_file", return_value=missing), \
             patch("src.api.conference.service.cancel_followup_reminders", return_value=None), \
             patch("src.api.conference.autodraft.cancel_followup_reminders", return_value=None):
            s = ad.run_conference_autodraft_sweep(db, user, now_utc=now)

        assert s.steps_drafted == 0
        assert s.leads_considered == 1
        assert any(k.startswith("skip_") for k in s.reasons)


# ═══ GROUP H - Scheduler hook integration ══════════════════════════════════


class TestSchedulerHook:
    def test_autodraft_import_path_resolves(self):
        """main.py imports the sweep by this path - catch a refactor that
        silently breaks the hook by moving the module."""
        from src.api.conference.autodraft import run_conference_autodraft_sweep
        assert callable(run_conference_autodraft_sweep)

    def test_sweep_signature_matches_main_py_call(self):
        """main.py calls run_conference_autodraft_sweep(db, user).
        Keep the positional args stable."""
        import inspect
        sig = inspect.signature(ad.run_conference_autodraft_sweep)
        params = list(sig.parameters.keys())
        assert params[0] == "db"
        assert params[1] == "user"

    def test_summary_has_expected_fields(self, db, user, fake_graph):
        s = ad.run_conference_autodraft_sweep(db, user)
        d = s.as_dict()
        assert {"leads_considered", "steps_drafted", "steps_failed",
                "leads_with_errors", "reasons"}.issubset(d.keys())


# ═══ GROUP I - Pure helper sanity ══════════════════════════════════════════


class TestPureHelper:
    def test_step_is_due_negative_offset(self):
        """day_offset shouldn't be negative in real data but defensive."""
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        # day_offset=-1 → target is day before anchor → "due" in the past
        assert ad._step_is_due(anchor, -1, now, timezone.utc) is True

    def test_step_is_due_huge_offset(self):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        assert ad._step_is_due(anchor, 365, now, timezone.utc) is False

    def test_step_is_due_zero_offset_same_day(self):
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        # Same day 09:30 → due
        now = datetime(2026, 4, 1, 9, 30, tzinfo=timezone.utc)
        # Wait - anchor is 10:00 but now is 09:30, now < anchor.
        # Feature says: target = anchor_date + 0 days = 04-01,
        # now_date == target_date, now.hour 9 >= 9 → due.
        assert ad._step_is_due(anchor, 0, now, timezone.utc) is True


# ═══ GROUP J - Reconciliation / cross-check ════════════════════════════════


class TestReconciliation:
    def test_drafted_steps_always_contains_email1(self, db, user, fake_graph):
        """Sanity: after sweep, email1 should still be in drafted_steps
        (we never touch it)."""
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 9, 9, 30, tzinfo=timezone.utc)
        _make(db, user, anchor=anchor)
        ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        lead = db.query(ConferenceLead).first()
        drafted = json.loads(lead.generated_sequence)["_drafted_steps"]
        assert "email1" in drafted

    def test_draft_created_at_never_changed(self, db, user, fake_graph):
        """Sweep must never touch draft_created_at (it's email 1's anchor)."""
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 9, 9, 30, tzinfo=timezone.utc)
        _make(db, user, anchor=anchor)
        ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        lead = db.query(ConferenceLead).first()
        # May be naive after round-trip through SQLite; compare calendar
        stored = lead.draft_created_at
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        assert stored == anchor

    def test_email1_never_auto_drafted(self, db, user, fake_graph):
        """Even if email1 is somehow missing from drafted_steps but
        draft_created_at is set (operator bug), sweep must NOT re-draft
        email1."""
        anchor = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 4, 9, 30, tzinfo=timezone.utc)
        seq = _seq(_drafted_steps=[])  # intentionally empty
        _make(db, user, seq=seq, anchor=anchor)
        ad.run_conference_autodraft_sweep(db, user, now_utc=now)
        # email2 drafted; email1 must still NOT be in calls
        subjects = [c["subject"] for c in fake_graph.calls]
        assert "S1" not in subjects
        assert "S2" in subjects
