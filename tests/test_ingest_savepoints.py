"""
P4-2 - savepoint isolation in ``ingest_event``.

The ingestion pipeline used to run every sub-step inside a single
``db.commit()``, so a failure in the reply classifier, the score
recompute, or the prospect-status sync would roll back the
EngagementEvent audit row. After P4-2 the event is committed first and
every sub-step runs inside its own SAVEPOINT. These tests verify that
property by monkeypatching individual sub-steps to raise and asserting
that the event + upstream state stay intact.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import Prospect, ProspectStatus
from src.orchestration import orm as _orch_orm  # noqa: F401  (register ORM models)
from src.orchestration import ingestion as ingestion_module
from src.orchestration.enums import OrchestrationStatus
from src.orchestration.ingestion import ingest_event
from src.orchestration.orm import EngagementEventORM, OrchSequenceStateORM


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _make_prospect(db, email: str = "lead@example.com") -> Prospect:
    p = Prospect(
        id=str(uuid.uuid4()),
        first_name="Nadia",
        last_name="Khoury",
        email=email,
        title="HSE Manager",
        company="GulfRefinery",
        industry="Oil & Gas",
        status=ProspectStatus.new,
        engagement_score=0,
    )
    db.add(p)
    db.commit()
    return p


def _make_active_state(db, lead_id: str) -> OrchSequenceStateORM:
    row = OrchSequenceStateORM(
        state_id=str(uuid.uuid4()),
        lead_id=lead_id,
        sequence_id="seq-test",
        status=OrchestrationStatus.active.value,
        current_step_index=0,
        enrolled_at=datetime.now(timezone.utc).replace(tzinfo=None),
        scheduled_next_at=None,
        engagement_score=0,
        stop_reason=None,
        paused_reason=None,
        completed_steps=[],
        consecutive_failure_count=0,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(row)
    db.commit()
    return row


def _open_payload(email: str) -> dict:
    return {
        "event_type": "email_opened",
        "email": email,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "campaign_id": "camp-1",
    }


def _reply_payload(email: str) -> dict:
    return {
        "event_type": "email_replied",
        "email": email,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reply_body": "Interested, let's chat.",
        "subject": "Re: HSE audits",
    }


def _bounce_payload(email: str) -> dict:
    return {
        "event_type": "email_bounced",
        "bounce_type": "hard",
        "email": email,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _unsub_payload(email: str) -> dict:
    return {
        "event_type": "unsubscribed",
        "email": email,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── P4-2 tests ──────────────────────────────────────────────────────────


def test_event_saved_even_if_state_transition_fails(session, monkeypatch):
    """A crash inside ``apply_event_to_state`` must not lose the event row."""
    prospect = _make_prospect(session)
    state = _make_active_state(session, prospect.id)
    status_before = state.status

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic state-transition failure")

    monkeypatch.setattr(ingestion_module, "apply_event_to_state", _boom)

    resp = ingest_event(_open_payload(prospect.email), session, provider="phantombuster")

    # Event is the audit record - it MUST be persisted.
    events = session.query(EngagementEventORM).all()
    assert len(events) == 1
    assert events[0].lead_id == prospect.id
    assert resp.event_id == events[0].event_id

    # State is untouched (status unchanged, engagement_score unchanged).
    session.refresh(state)
    assert state.status == status_before

    # The actions list surfaces the saved event and does NOT claim a
    # state transition happened.
    assert any(a.startswith("event_saved_") for a in resp.actions_triggered)
    assert not any(
        a.startswith("state_transitioned_to_") for a in resp.actions_triggered
    )


def test_reply_creation_failure_does_not_lose_event(session, monkeypatch):
    """If the reply classifier path blows up, event still persists and no
    Reply row is created."""
    from src.db.models import Reply

    prospect = _make_prospect(session)
    _make_active_state(session, prospect.id)

    # Force the savepoint to fail inside _classify_and_create_reply by
    # patching the helper to raise *after* the initial work. The simplest
    # way: monkeypatch the ORM Reply() constructor to raise.
    def _explode(*args, **kwargs):
        raise RuntimeError("synthetic reply-row failure")

    import src.orchestration.ingestion as _ing
    monkeypatch.setattr(
        _ing,
        "_classify_and_create_reply",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    resp = ingest_event(
        _reply_payload(prospect.email), session, provider="phantombuster"
    )

    # Event is saved.
    events = session.query(EngagementEventORM).all()
    assert len(events) == 1

    # No Reply row was created (savepoint rolled back).
    replies = session.query(Reply).all()
    assert replies == []

    # No exception bubbles up - the caller got a normal response.
    assert resp.lead_id == prospect.id


def test_score_recompute_failure_isolated(session, monkeypatch):
    """If ``compute_score`` raises, the event and prospect status sync must
    still be committed."""
    prospect = _make_prospect(session)
    _make_active_state(session, prospect.id)

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic scorer failure")

    monkeypatch.setattr(ingestion_module, "compute_score", _boom)

    resp = ingest_event(
        _open_payload(prospect.email), session, provider="phantombuster"
    )

    # Event persisted.
    events = session.query(EngagementEventORM).all()
    assert len(events) == 1

    # Prospect status sync ran before the scorer - opened status should have
    # been applied even though the later score recompute blew up.
    session.refresh(prospect)
    assert prospect.status == ProspectStatus.opened

    # Score was not updated (savepoint rolled back).
    assert "score_updated_0" not in resp.actions_triggered
    assert any(a.startswith("event_saved_") for a in resp.actions_triggered)
    assert "prospect_status_synced_opened" in resp.actions_triggered


def test_savepoint_rollback_only_affects_that_step(session, monkeypatch):
    """Inducing a failure inside the suppression/sync savepoint must leave
    every *other* sub-step's writes intact."""
    from src.db.models import UnsubscribeEntry

    prospect = _make_prospect(session)
    _make_active_state(session, prospect.id)

    # Patch _sync_prospect_status to partially write then raise - simulating
    # a mid-function crash inside the savepoint.
    def _bad_sync(db, prospect, lead, event_type, occurred_at, email_from_payload, actions_triggered):
        prospect.status = ProspectStatus.unsubscribed  # write
        db.add(
            UnsubscribeEntry(
                email=email_from_payload,
                reason="webhook_unsubscribe",
                source="webhook",
            )
        )
        raise RuntimeError("synthetic suppression failure")

    monkeypatch.setattr(ingestion_module, "_sync_prospect_status", _bad_sync)

    resp = ingest_event(
        _unsub_payload(prospect.email), session, provider="phantombuster"
    )

    # Event row committed (it was saved before any savepoint).
    events = session.query(EngagementEventORM).all()
    assert len(events) == 1
    assert resp.lead_id == prospect.id

    # The partial writes inside the savepoint were rolled back.
    session.expire_all()
    refreshed = session.query(Prospect).filter_by(id=prospect.id).first()
    assert refreshed.status != ProspectStatus.unsubscribed  # rolled back

    # The UnsubscribeEntry added inside the failing savepoint is gone.
    assert session.query(UnsubscribeEntry).count() == 0

    # Actions list reflects the savepoint failure (no sync action appended).
    assert not any(
        a.startswith("prospect_status_synced_") for a in resp.actions_triggered
    )
    assert any(a.startswith("event_saved_") for a in resp.actions_triggered)
