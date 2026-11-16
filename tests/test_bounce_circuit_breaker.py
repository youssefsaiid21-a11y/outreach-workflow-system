"""
Hardening 2.2 - bounce-rate circuit breaker.

Pins:
1. Below the threshold (or below the min-sample floor): does NOT pause.
2. Above the threshold AND above min-sample: pauses the local campaign.
3. Surfaces in actions_triggered with the percentage so the watchdog
   can pick it up.
4. Local pause lands even if the remote pause call throws.

Note: Instantly has been removed. The circuit breaker now only writes the
local DB pause; tests that previously verified Instantly API calls have
been updated accordingly.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

# Dedicated file-based SQLite for isolation from the global engine.
import tempfile
_TEST_DB_PATH = Path(tempfile.gettempdir()) / f"outreach_test_bounce_{os.getpid()}.db"
if _TEST_DB_PATH.exists():
    _TEST_DB_PATH.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"

from src.db.database import SessionLocal, init_db  # noqa: E402
init_db()


def _purge_all():
    from src.db.models import (
        Campaign,
        EmailSequence,
        Prospect,
        SendLog,
        User,
    )
    from src.orchestration.orm import EngagementEventORM
    db = SessionLocal()
    try:
        for model in (EngagementEventORM, SendLog, EmailSequence, Prospect, Campaign, User):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def db_session():
    _purge_all()
    db = SessionLocal()
    try:
        yield db
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def _seed(db, *, n_sends=30, n_bounces=5):
    """Create a campaign + N send_logs + M bounce engagement events."""
    from src.db.models import (
        Campaign,
        CampaignStatus,
        EmailSequence,
        Prospect,
        ProspectStatus,
        SendLog,
        SequenceStatus,
        User,
        UserRole,
    )
    from src.orchestration.orm import EngagementEventORM
    from src.orchestration.enums import EventType

    user_id = str(uuid.uuid4())
    db.add(User(
        id=user_id, name="Rep", email=f"rep-{uuid.uuid4().hex[:8]}@x.com", password_hash="x",
        role=UserRole.admin, created_at=datetime.now(timezone.utc),
    ))
    db.flush()
    cid = str(uuid.uuid4())
    db.add(Campaign(
        id=cid, name="Bounce Test",
        goal="m", value_prop="v",
        status=CampaignStatus.active,
        user_id=user_id, inbox_ids=["s@x.com"],
        created_at=datetime.now(timezone.utc),
    ))
    db.flush()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    last_pid = None
    for i in range(n_sends):
        pid = str(uuid.uuid4())
        db.add(Prospect(
            id=pid, email=f"l{i}-{uuid.uuid4().hex[:6]}@x.com",
            first_name="L", last_name="O",
            status=ProspectStatus.enrolled, user_id=user_id,
            created_at=now,
        ))
        db.flush()
        sid = str(uuid.uuid4())
        db.add(EmailSequence(
            id=sid, campaign_id=cid, prospect_id=pid,
            approved=True, status=SequenceStatus.enrolled,
            email1_subject="s", email1_body="b",
            created_at=now,
        ))
        db.flush()
        db.add(SendLog(
            id=str(uuid.uuid4()),
            prospect_id=pid, campaign_id=cid, sequence_id=sid,
            step=1, subject="s", body_preview="b",
            status="sent", enrolled_at=now, sent_at=now,
            inbox_email="s@x.com",
        ))
        db.flush()
        last_pid = pid

    # Insert N bounce events
    for i in range(n_bounces):
        db.add(EngagementEventORM(
            event_id=str(uuid.uuid4()),
            lead_id=last_pid,  # any prospect that exists in the campaign
            event_type=EventType.email_bounced_hard.value,
            occurred_at=now,
            ingested_at=now,
            event_metadata={"campaign_id": cid},
            raw_payload={},
        ))
    db.commit()
    return {"user_id": user_id, "campaign_id": cid, "last_prospect_id": last_pid}


def _make_lead(prospect_id):
    from src.orchestration.models import LeadContext
    return LeadContext(
        lead_id=prospect_id, email="l@x.com",
        first_name="L", last_name="O", company="C", title="T",
        industry="", location="",
    )


def test_below_threshold_does_not_pause(db_session):
    """30 sends, 1 bounce → 3.3% bounce rate, well below 10%. Must NOT pause."""
    from src.orchestration.ingestion import _check_bounce_rate_circuit_breaker
    from src.db.models import Campaign, CampaignStatus

    seeded = _seed(db_session, n_sends=30, n_bounces=1)
    actions: list[str] = []

    _check_bounce_rate_circuit_breaker(
        db=db_session,
        lead=_make_lead(seeded["last_prospect_id"]),
        occurred_at=datetime.now(timezone.utc),
        actions_triggered=actions,
    )

    assert not any("bounce_circuit_breaker" in a for a in actions)
    camp = db_session.query(Campaign).filter_by(id=seeded["campaign_id"]).first()
    assert camp.status == CampaignStatus.active


def test_below_min_sends_does_not_pause(db_session):
    """5 sends, 5 bounces (100% rate) but below the min-sends floor of 20.
    Must NOT pause - the sample is too small."""
    from src.orchestration.ingestion import _check_bounce_rate_circuit_breaker
    from src.db.models import Campaign, CampaignStatus

    seeded = _seed(db_session, n_sends=5, n_bounces=5)
    actions: list[str] = []

    _check_bounce_rate_circuit_breaker(
        db=db_session,
        lead=_make_lead(seeded["last_prospect_id"]),
        occurred_at=datetime.now(timezone.utc),
        actions_triggered=actions,
    )

    assert not any("bounce_circuit_breaker" in a for a in actions)
    camp = db_session.query(Campaign).filter_by(id=seeded["campaign_id"]).first()
    assert camp.status == CampaignStatus.active


def test_above_threshold_pauses_campaign(db_session):
    """30 sends, 5 bounces → 16.7% bounce rate, above 10%. Pauses."""
    from src.orchestration.ingestion import _check_bounce_rate_circuit_breaker
    from src.db.models import Campaign, CampaignStatus

    seeded = _seed(db_session, n_sends=30, n_bounces=5)
    actions: list[str] = []

    _check_bounce_rate_circuit_breaker(
        db=db_session,
        lead=_make_lead(seeded["last_prospect_id"]),
        occurred_at=datetime.now(timezone.utc),
        actions_triggered=actions,
    )

    assert any(a.startswith("bounce_circuit_breaker_paused_") for a in actions), (
        f"expected bounce_circuit_breaker_paused_ in actions, got {actions}"
    )
    camp = db_session.query(Campaign).filter_by(id=seeded["campaign_id"]).first()
    assert camp.status == CampaignStatus.paused


def test_local_pause_lands_on_circuit_breaker_trigger(db_session):
    """The circuit breaker must always write the local DB pause regardless
    of any downstream side effects."""
    from src.orchestration.ingestion import _check_bounce_rate_circuit_breaker
    from src.db.models import Campaign, CampaignStatus

    seeded = _seed(db_session, n_sends=30, n_bounces=5)
    actions: list[str] = []

    _check_bounce_rate_circuit_breaker(
        db=db_session,
        lead=_make_lead(seeded["last_prospect_id"]),
        occurred_at=datetime.now(timezone.utc),
        actions_triggered=actions,
    )

    camp = db_session.query(Campaign).filter_by(id=seeded["campaign_id"]).first()
    assert camp.status == CampaignStatus.paused
    # Action still recorded so the watchdog can pick it up
    assert any(a.startswith("bounce_circuit_breaker_paused_") for a in actions)
