"""
Tests for the GTM Analytics aggregation service and API endpoints.

Covers:
  - Monthly bucketing for each of the 5 categories
  - Manual entry CRUD
  - Filter by user, category, conference
  - Grand total meetings calculation
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
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("MICROSOFT_CLIENT_ID", "test-client-id")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.database import Base, get_db
from src.db.models import (
    User, UserRole, GtmManualEntry,
    LinkedInQueue, LinkedInQueueStatus,
    SendLog, EmailSequence, SequenceStatus,
    Campaign, CampaignStatus,
    ConferenceLead, Reply, ReplyStatus,
)
from src.services.gtm_aggregator import (
    aggregate_linkedin, aggregate_cold_email, aggregate_inbound,
    aggregate_conference, aggregate_referral, build_dashboard,
    _month_range,
)
from src.api.auth import get_current_user
from src.api.gtm_analytics import router

from fastapi import FastAPI
from fastapi.testclient import TestClient
from datetime import date


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def SL(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db(SL):
    session = SL()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(db):
    u = User(
        id=str(uuid.uuid4()), name="Test Rep", email="demo-member-010@company.example",
        password_hash="x", role=UserRole.rep,
    )
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def app(engine, user):
    application = FastAPI()
    application.include_router(router)

    SL = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _get_db():
        s = SL()
        try:
            yield s
        finally:
            s.close()

    def _get_user():
        return user

    application.dependency_overrides[get_db] = _get_db
    application.dependency_overrides[get_current_user] = _get_user
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


# ── Helper ────────────────────────────────────────────────────────────

def _dt(month_str, day=15):
    y, m = month_str.split("-")
    return datetime(int(y), int(m), day, 12, 0, 0, tzinfo=timezone.utc)


# ── Unit tests: aggregators ───────────────────────────────────────────

def test_month_range():
    assert _month_range(date(2026, 1, 1), date(2026, 3, 31)) == ["2026-01", "2026-02", "2026-03"]
    assert _month_range(date(2026, 12, 1), date(2026, 12, 31)) == ["2026-12"]


def test_aggregate_linkedin(db, user):
    # Add LinkedIn queue items
    for i in range(3):
        db.add(LinkedInQueue(
            id=str(uuid.uuid4()), prospect_id=str(uuid.uuid4()),
            user_id=user.id, action_type="connect", linkedin_url=f"https://linkedin.com/in/p{i}",
            status=LinkedInQueueStatus.done,
            created_at=_dt("2026-01"), processed_at=_dt("2026-01"),
        ))
    db.add(LinkedInQueue(
        id=str(uuid.uuid4()), prospect_id=str(uuid.uuid4()),
        user_id=user.id, action_type="message", linkedin_url="https://linkedin.com/in/p10",
        status=LinkedInQueueStatus.done,
        created_at=_dt("2026-02"), processed_at=_dt("2026-02"),
        result_detail="accepted",
    ))
    db.commit()

    result = aggregate_linkedin(db, date(2026, 1, 1), date(2026, 3, 31), user.id)
    assert result["connections_sent"]["2026-01"] == 3
    assert result["connections_sent"]["2026-02"] == 0
    assert result["connections_sent"]["cumulative"] == 3
    assert result["messages_sent"]["2026-02"] == 1


def test_aggregate_cold_email(db, user):
    camp = Campaign(
        id=str(uuid.uuid4()), user_id=user.id, name="Test",
        goal="test", value_prop="test", status=CampaignStatus.active,
    )
    db.add(camp)

    # Step 1 send (first email)
    db.add(SendLog(
        id=str(uuid.uuid4()), user_id=user.id, campaign_id=camp.id,
        prospect_id=str(uuid.uuid4()), sequence_id=str(uuid.uuid4()),
        step=1, status="sent", sent_at=_dt("2026-01"),
    ))
    # Step 2 send (follow-up)
    db.add(SendLog(
        id=str(uuid.uuid4()), user_id=user.id, campaign_id=camp.id,
        prospect_id=str(uuid.uuid4()), sequence_id=str(uuid.uuid4()),
        step=2, status="sent", sent_at=_dt("2026-01"),
    ))
    # Bounced (EmailSequence with status=bounced, matching how production works)
    bounced_prospect_id = str(uuid.uuid4())
    db.add(EmailSequence(
        id=str(uuid.uuid4()), campaign_id=camp.id, prospect_id=bounced_prospect_id,
        status=SequenceStatus.bounced, created_at=_dt("2026-01"),
    ))
    db.commit()

    result = aggregate_cold_email(db, date(2026, 1, 1), date(2026, 3, 31), user.id)
    assert result["first_emails_sent"]["2026-01"] == 1
    assert result["follow_ups_sent"]["2026-01"] == 1
    assert result["bounced"]["2026-01"] == 1


def test_aggregate_inbound(db):
    db.add(GtmManualEntry(
        id=str(uuid.uuid4()), category="inbound", metric_key="inbound_lead",
        month="2026-02", value=1, source_ref="lead@example.com",
        created_at=_dt("2026-02"), updated_at=_dt("2026-02"),
    ))
    db.add(GtmManualEntry(
        id=str(uuid.uuid4()), category="inbound", metric_key="inbound_lead",
        month="2026-02", value=1, source_ref="other@example.com",
        created_at=_dt("2026-02"), updated_at=_dt("2026-02"),
    ))
    db.commit()

    result = aggregate_inbound(db, date(2026, 1, 1), date(2026, 3, 31))
    assert result["leads_registered"]["2026-02"] == 2
    assert result["leads_registered"]["cumulative"] == 2


def test_aggregate_conference(db, user):
    db.add(ConferenceLead(
        id=str(uuid.uuid4()), conference_name="ADIPEC2026",
        first_name="A", email="a@test.com", user_id=user.id,
        draft_created_at=_dt("2026-03"), replied_at=_dt("2026-03"),
        meeting_booked=True, created_at=_dt("2026-03"),
    ))
    db.add(ConferenceLead(
        id=str(uuid.uuid4()), conference_name="ADIPEC2026",
        first_name="B", email="b@test.com", user_id=user.id,
        draft_created_at=_dt("2026-03"), created_at=_dt("2026-03"),
    ))
    db.commit()

    result = aggregate_conference(db, date(2026, 1, 1), date(2026, 3, 31), user.id)
    assert result["emails_sent"]["2026-03"] == 2
    assert result["responses"]["2026-03"] == 1
    assert result["meetings_booked"]["2026-03"] == 1
    assert len(result["by_conference"]) == 1
    assert result["by_conference"][0]["conference_name"] == "ADIPEC2026"


def test_aggregate_referral(db, user):
    db.add(GtmManualEntry(
        id=str(uuid.uuid4()), user_id=user.id, category="referral",
        metric_key="referral_meetings", month="2026-01", value=3,
        created_at=_dt("2026-01"), updated_at=_dt("2026-01"),
    ))
    db.commit()

    result = aggregate_referral(db, date(2026, 1, 1), date(2026, 3, 31), user.id)
    assert result["meetings_booked"]["2026-01"] == 3
    assert result["meetings_booked"]["cumulative"] == 3


def test_build_dashboard_grand_total(db, user):
    # Referral: 2 meetings in Jan
    db.add(GtmManualEntry(
        id=str(uuid.uuid4()), user_id=user.id, category="referral",
        metric_key="referral_meetings", month="2026-01", value=2,
        created_at=_dt("2026-01"), updated_at=_dt("2026-01"),
    ))
    # LinkedIn: 1 meeting in Feb
    db.add(GtmManualEntry(
        id=str(uuid.uuid4()), user_id=user.id, category="linkedin",
        metric_key="meetings_booked", month="2026-02", value=1,
        created_at=_dt("2026-02"), updated_at=_dt("2026-02"),
    ))
    db.commit()

    result = build_dashboard(db, date(2026, 1, 1), date(2026, 3, 31), user.id)
    assert result["grand_total_meetings"]["2026-01"] == 2
    assert result["grand_total_meetings"]["2026-02"] == 1
    assert result["grand_total_meetings"]["cumulative"] == 3


# ── API endpoint tests ───────────────────────────────────────────────

def test_summary_endpoint(client):
    r = client.get("/gtm-analytics/summary?start_date=2026-01-01&end_date=2026-03-31")
    assert r.status_code == 200
    data = r.json()
    assert "months" in data
    assert "sections" in data
    assert "grand_total_meetings" in data


def test_conferences_endpoint(client):
    r = client.get("/gtm-analytics/conferences")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_manual_entry_crud(client):
    # Create
    r = client.post("/gtm-analytics/entries", json={
        "category": "referral",
        "metric_key": "referral_meetings",
        "month": "2026-05",
        "value": 5,
        "note": "From Ahmed",
    })
    assert r.status_code == 200
    entry = r.json()
    assert entry["value"] == 5
    entry_id = entry["id"]

    # List
    r = client.get("/gtm-analytics/entries?category=referral")
    assert r.status_code == 200
    assert len(r.json()) >= 1

    # Update
    r = client.put(f"/gtm-analytics/entries/{entry_id}", json={"value": 8})
    assert r.status_code == 200
    assert r.json()["value"] == 8

    # Upsert (same key → should update existing)
    r = client.post("/gtm-analytics/entries", json={
        "category": "referral",
        "metric_key": "referral_meetings",
        "month": "2026-05",
        "value": 10,
    })
    assert r.status_code == 200
    assert r.json()["value"] == 10

    # Delete
    r = client.delete(f"/gtm-analytics/entries/{entry_id}")
    assert r.status_code == 200


def test_invalid_category_rejected(client):
    r = client.post("/gtm-analytics/entries", json={
        "category": "invalid",
        "metric_key": "test",
        "month": "2026-05",
        "value": 1,
    })
    assert r.status_code == 400


def test_invalid_month_rejected(client):
    r = client.post("/gtm-analytics/entries", json={
        "category": "referral",
        "metric_key": "referral_meetings",
        "month": "May2026",
        "value": 1,
    })
    assert r.status_code == 400
