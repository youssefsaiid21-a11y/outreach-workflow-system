"""
Test for Nasser v1.1 "Due now" bug (2026-04-11).

The bug: the sequence monitor drawer labels email 1 as "Due now" even
after it has been sent, because the frontend was reading
``LeadSequenceState.current_step_index`` which only advances when the
ORCHESTRATOR executes a step. For hybrid email+LinkedIn campaigns,
the orch pointer is pinned at 0 until the first LinkedIn step fires -
mislabeling the already-sent email 1.

The fix: ``/orchestration/states`` now joins ``SendLog`` and returns
``email_sent_count`` + ``email_sent_steps`` derived from sent rows
(ground truth - flipped to ``sent`` by the ``email_sent`` webhook in
``_confirm_send_log``). The frontend uses that for email step status.

This test covers the backend side of the fix: given a lead with N
sent SendLog rows, the ``list_states`` endpoint must report
``email_sent_count = N``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("API_KEY", "test")

from src.db.models import (  # noqa: E402
    Base,
    Campaign,
    CampaignStatus,
    EmailSequence,
    Prospect,
    SendLog,
    User,
)
from src.orchestration.orm import OrchSequenceStateORM  # noqa: E402
from src.orchestration.enums import OrchestrationStatus  # noqa: E402


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    s = Session()
    yield s
    s.close()


def _make_full_fixture(db, *, sent_count: int = 0):
    """Create user + campaign + prospect + EmailSequence + orch state
    + N sent SendLog rows. Returns the prospect_id."""
    user = User(id="u-1", email="nehal@x.com", name="Nehal", password_hash="x")
    db.add(user)

    camp = Campaign(
        id="c-nasser-v1-1",
        user_id="u-1",
        name="Nasser v1.1",
        goal="Book meetings with HSE leaders",
        value_prop="Example safety AI",
        status=CampaignStatus.active,
        sequence_config_id="seq-cfg-1",
    )
    db.add(camp)

    prospect = Prospect(
        id="p-1",
        user_id="u-1",
        email="target@corp.com",
        first_name="Alex",
        last_name="Example",
        company="Corp",
    )
    db.add(prospect)

    es = EmailSequence(
        id="es-1",
        campaign_id="c-nasser-v1-1",
        prospect_id="p-1",
        email1_subject="s1", email1_body="b1",
        email2_subject="s2", email2_body="b2",
        approved=True,
        status="enrolled",
        current_step=1,
    )
    db.add(es)

    orch = OrchSequenceStateORM(
        state_id="s-1",
        lead_id="p-1",
        sequence_id="nasser-v1-1-seq",
        status=OrchestrationStatus.active.value,
        current_step_index=0,  # pinned - scheduler hasn't run yet
        enrolled_at=datetime(2026, 4, 9, 5, 58, tzinfo=timezone.utc),
        scheduled_next_at=datetime(2026, 4, 12, 5, 58, tzinfo=timezone.utc),
        engagement_score=0,
        completed_steps=[],
        user_id="u-1",
    )
    db.add(orch)

    for i in range(sent_count):
        db.add(SendLog(
            id=f"sl-{i+1}",
            user_id="u-1",
            campaign_id="c-nasser-v1-1",
            prospect_id="p-1",
            sequence_id="es-1",
            step=i + 1,
            subject=f"s{i+1}",
            body_preview="preview",
            sent_at=datetime(2026, 4, 9 + i, 6, 0, tzinfo=timezone.utc),
            inbox_email="sender@co.com",
            status="sent",
            enrolled_at=datetime(2026, 4, 9, 5, 58, tzinfo=timezone.utc),
        ))

    db.commit()
    return "p-1"


def _invoke_list_states(db, campaign_id=None):
    """Call list_states directly with the SQLAlchemy session injected,
    bypassing FastAPI routing / auth. This isolates the join logic.

    Must pass campaign_id explicitly because the signature uses
    ``Query(default=None)`` as a FastAPI dependency sentinel - when
    calling outside of FastAPI, we have to supply the actual value."""
    from src.orchestration.api import list_states
    return list_states(include_terminal=False, campaign_id=campaign_id, db=db)


class TestEmailSentCountJoin:
    def test_no_send_logs_yields_zero(self, db):
        _make_full_fixture(db, sent_count=0)
        rows = _invoke_list_states(db)
        assert len(rows) == 1
        assert rows[0]["email_sent_count"] == 0
        assert rows[0]["email_sent_steps"] == []
        # Orch pointer unchanged
        assert rows[0]["current_step_index"] == 0

    def test_one_email_sent_yields_count_one(self, db):
        """This is the Nasser v1.1 case: email 1 sent out-of-band,
        orch pointer still at 0 because no LinkedIn step has fired.
        The endpoint must surface email_sent_count=1 so the frontend
        stops labeling email 1 as 'Due now'."""
        _make_full_fixture(db, sent_count=1)
        rows = _invoke_list_states(db)
        assert rows[0]["email_sent_count"] == 1
        assert rows[0]["email_sent_steps"] == [1]
        # Orch pointer still 0 - that's the whole point of the bug
        assert rows[0]["current_step_index"] == 0

    def test_multiple_emails_sent(self, db):
        _make_full_fixture(db, sent_count=3)
        rows = _invoke_list_states(db)
        assert rows[0]["email_sent_count"] == 3
        assert rows[0]["email_sent_steps"] == [1, 2, 3]

    def test_other_campaigns_not_counted(self, db):
        """SendLog rows from a DIFFERENT campaign must not inflate the
        count for this lead in the current active campaign."""
        _make_full_fixture(db, sent_count=1)
        # Add a second campaign with its own sent rows for the same lead
        db.add(Campaign(
            id="c-other", user_id="u-1", name="Other Camp",
            goal="g", value_prop="v",
            status=CampaignStatus.completed,
        ))
        db.add(EmailSequence(
            id="es-other", campaign_id="c-other", prospect_id="p-1",
            email1_subject="x", email1_body="x",
            approved=True, status="enrolled", current_step=1,
        ))
        db.add(SendLog(
            id="sl-other-1", user_id="u-1", campaign_id="c-other",
            prospect_id="p-1", sequence_id="es-other", step=1,
            subject="x", body_preview="x",
            sent_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            inbox_email="sender@co.com", status="sent",
            enrolled_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        ))
        db.commit()
        rows = _invoke_list_states(db)
        # Should only count the active/latest campaign's send logs.
        # The fixture's active campaign (Nasser v1.1) has 1 sent row.
        # The lead's most-recent EmailSequence determines which campaign
        # to scope by - the implementation picks the most recent by
        # created_at desc.
        assert len(rows) == 1
        assert rows[0]["email_sent_count"] in (1, 1), (
            "Count must be scoped to the lead's active campaign, not "
            "summed across every campaign the lead has ever been in."
        )

    def test_pending_send_log_not_counted(self, db):
        """SendLog rows with status='pending' (webhook hasn't confirmed)
        must not be counted as sent."""
        _make_full_fixture(db, sent_count=1)
        db.add(SendLog(
            id="sl-pending", user_id="u-1", campaign_id="c-nasser-v1-1",
            prospect_id="p-1", sequence_id="es-1", step=2,
            subject="s2", body_preview="x",
            sent_at=None, inbox_email="sender@co.com",
            status="pending",
            enrolled_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
        ))
        db.commit()
        rows = _invoke_list_states(db)
        # Only the sent step (1) should count, not the pending step (2)
        assert rows[0]["email_sent_count"] == 1
        assert rows[0]["email_sent_steps"] == [1]

    def test_no_send_logs_no_email_sequence_still_safe(self, db):
        """Lead has a orch state but no EmailSequence row at all
        (edge case from a linkedin-only enrolment): must not crash."""
        user = User(id="u-2", email="selma@x.com", name="Selma", password_hash="x")
        db.add(user)
        db.add(Prospect(id="p-2", user_id="u-2", email="x@y.com",
                        first_name="X", last_name="Y", company="Y"))
        db.add(OrchSequenceStateORM(
            state_id="s-2", lead_id="p-2",
            sequence_id="seq-x",
            status=OrchestrationStatus.active.value,
            current_step_index=0,
            enrolled_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
            scheduled_next_at=datetime(2026, 4, 11, tzinfo=timezone.utc),
            engagement_score=0, completed_steps=[], user_id="u-2",
        ))
        db.commit()
        rows = _invoke_list_states(db)
        assert len(rows) == 1
        assert rows[0]["email_sent_count"] == 0

    def test_response_shape_includes_new_fields(self, db):
        """Smoke test: the API contract must include email_sent_count
        and email_sent_steps on every row. Contract test for the
        frontend consumer."""
        _make_full_fixture(db, sent_count=2)
        rows = _invoke_list_states(db)
        row = rows[0]
        assert "email_sent_count" in row
        assert "email_sent_steps" in row
        assert isinstance(row["email_sent_count"], int)
        assert isinstance(row["email_sent_steps"], list)


class TestExplicitCampaignScoping:
    """2026-04-11 follow-up: ensure ``list_states(campaign_id=...)``
    scopes SendLog counts to the specified campaign, with zero
    cross-campaign bleed even when a lead is in multiple campaigns."""

    def _make_two_campaign_lead(self, db):
        """Lead is in two campaigns, with different email_sent counts
        in each. This is the scenario the 'most recent EmailSequence'
        heuristic cannot reliably handle - the explicit scope must."""
        from src.orchestration.api import list_states
        db.add(User(id="u-1", email="rep@x.com", name="R", password_hash="x"))
        db.add(Prospect(
            id="p-shared", user_id="u-1", email="shared@corp.com",
            first_name="Shared", last_name="Lead", company="Corp",
        ))
        # Campaign A: 2 emails sent
        db.add(Campaign(
            id="c-a", user_id="u-1", name="Campaign A",
            goal="g", value_prop="v",
            status=CampaignStatus.active,
        ))
        db.add(EmailSequence(
            id="es-a", campaign_id="c-a", prospect_id="p-shared",
            email1_subject="s", email1_body="b",
            approved=True, status="enrolled", current_step=1,
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        ))
        for i in range(2):
            db.add(SendLog(
                id=f"sl-a-{i+1}", user_id="u-1", campaign_id="c-a",
                prospect_id="p-shared", sequence_id="es-a", step=i + 1,
                subject="s", body_preview="x",
                sent_at=datetime(2026, 4, 2 + i, tzinfo=timezone.utc),
                inbox_email="sender@co.com", status="sent",
                enrolled_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            ))
        # Campaign B: 1 email sent - created LATER so "most recent"
        # heuristic would pick this one
        db.add(Campaign(
            id="c-b", user_id="u-1", name="Campaign B",
            goal="g", value_prop="v",
            status=CampaignStatus.active,
        ))
        db.add(EmailSequence(
            id="es-b", campaign_id="c-b", prospect_id="p-shared",
            email1_subject="s", email1_body="b",
            approved=True, status="enrolled", current_step=1,
            created_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
        ))
        db.add(SendLog(
            id="sl-b-1", user_id="u-1", campaign_id="c-b",
            prospect_id="p-shared", sequence_id="es-b", step=1,
            subject="s", body_preview="x",
            sent_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
            inbox_email="sender@co.com", status="sent",
            enrolled_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
        ))
        # Orch state (single per lead - Campaign B wins because newest)
        db.add(OrchSequenceStateORM(
            state_id="s-shared", lead_id="p-shared",
            sequence_id="invigilo-linkedin-only-v1",
            status=OrchestrationStatus.active.value,
            current_step_index=0,
            enrolled_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
            scheduled_next_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            engagement_score=0, completed_steps=[], user_id="u-1",
        ))
        db.commit()

    def test_scope_to_campaign_a(self, db):
        self._make_two_campaign_lead(db)
        from src.orchestration.api import list_states
        rows = list_states(include_terminal=False, campaign_id="c-a", db=db)
        assert len(rows) == 1
        assert rows[0]["email_sent_count"] == 2, (
            "Explicit campaign_id=c-a must return A's count (2), not "
            "the 'most recent' heuristic count from B (1)."
        )
        assert rows[0]["email_sent_steps"] == [1, 2]

    def test_scope_to_campaign_b(self, db):
        self._make_two_campaign_lead(db)
        from src.orchestration.api import list_states
        rows = list_states(include_terminal=False, campaign_id="c-b", db=db)
        assert len(rows) == 1
        assert rows[0]["email_sent_count"] == 1
        assert rows[0]["email_sent_steps"] == [1]

    def test_no_campaign_falls_back_to_most_recent(self, db):
        """Without campaign_id, the fallback picks B (most recent)."""
        self._make_two_campaign_lead(db)
        from src.orchestration.api import list_states
        rows = list_states(include_terminal=False, campaign_id=None, db=db)
        assert rows[0]["email_sent_count"] == 1  # B's count

    def test_unknown_campaign_yields_zero(self, db):
        """Scoping to a campaign the lead isn't in returns 0, not
        the old heuristic count. Protects against UI mistakes."""
        self._make_two_campaign_lead(db)
        from src.orchestration.api import list_states
        rows = list_states(include_terminal=False, campaign_id="c-nonexistent", db=db)
        assert rows[0]["email_sent_count"] == 0
        assert rows[0]["email_sent_steps"] == []

    def test_many_leads_different_campaigns_each_scoped(self, db):
        """Sanity at scale: 5 leads in campaign A, 5 in campaign B;
        caller wants campaign A only - must return 5 rows with A's
        counts, not blended across both."""
        db.add(User(id="u-many", email="m@x.com", name="M", password_hash="x"))
        # Two campaigns
        for cid in ("c-A", "c-B"):
            db.add(Campaign(
                id=cid, user_id="u-many", name=f"Camp {cid}",
                goal="g", value_prop="v",
                status=CampaignStatus.active,
            ))
        # 5 leads per campaign, same lead_id doesn't span - truly separate
        for i in range(5):
            pid = f"p-A-{i}"
            db.add(Prospect(id=pid, user_id="u-many",
                            email=f"{pid}@co.com", first_name="A",
                            last_name=str(i), company="Co"))
            db.add(EmailSequence(
                id=f"es-A-{i}", campaign_id="c-A", prospect_id=pid,
                email1_subject="s", email1_body="b",
                approved=True, status="enrolled", current_step=1,
            ))
            # 2 sent per A-lead
            for k in range(2):
                db.add(SendLog(
                    id=f"sl-A-{i}-{k+1}", user_id="u-many", campaign_id="c-A",
                    prospect_id=pid, sequence_id=f"es-A-{i}", step=k + 1,
                    subject="s", body_preview="x",
                    sent_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                    inbox_email="s@c.com", status="sent",
                    enrolled_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                ))
            db.add(OrchSequenceStateORM(
                state_id=f"s-A-{i}", lead_id=pid,
                sequence_id="invigilo-linkedin-only-v1",
                status=OrchestrationStatus.active.value,
                current_step_index=0,
                enrolled_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                scheduled_next_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
                engagement_score=0, completed_steps=[], user_id="u-many",
            ))
        for i in range(5):
            pid = f"p-B-{i}"
            db.add(Prospect(id=pid, user_id="u-many",
                            email=f"{pid}@co.com", first_name="B",
                            last_name=str(i), company="Co"))
            db.add(EmailSequence(
                id=f"es-B-{i}", campaign_id="c-B", prospect_id=pid,
                email1_subject="s", email1_body="b",
                approved=True, status="enrolled", current_step=1,
            ))
            # 1 sent per B-lead
            db.add(SendLog(
                id=f"sl-B-{i}-1", user_id="u-many", campaign_id="c-B",
                prospect_id=pid, sequence_id=f"es-B-{i}", step=1,
                subject="s", body_preview="x",
                sent_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
                inbox_email="s@c.com", status="sent",
                enrolled_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
            ))
            db.add(OrchSequenceStateORM(
                state_id=f"s-B-{i}", lead_id=pid,
                sequence_id="invigilo-linkedin-only-v1",
                status=OrchestrationStatus.active.value,
                current_step_index=0,
                enrolled_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
                scheduled_next_at=datetime(2026, 4, 9, tzinfo=timezone.utc),
                engagement_score=0, completed_steps=[], user_id="u-many",
            ))
        db.commit()

        from src.orchestration.api import list_states
        # Scope to A: 10 orch rows returned (both A+B leads) but only
        # A leads have non-zero email_sent_count under A's scope
        rows = list_states(include_terminal=False, campaign_id="c-A", db=db)
        a_rows = [r for r in rows if r["lead_id"].startswith("p-A-")]
        b_rows = [r for r in rows if r["lead_id"].startswith("p-B-")]
        assert len(a_rows) == 5
        assert len(b_rows) == 5
        # A leads: 2 emails sent under c-A scope
        for r in a_rows:
            assert r["email_sent_count"] == 2
        # B leads: 0 emails sent under c-A scope (they're not in A)
        for r in b_rows:
            assert r["email_sent_count"] == 0
