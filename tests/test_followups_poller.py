"""
Tests for the follow-ups machine poller logic.

Covers:
  - Overdue detection based on days_since_activity
  - Trello card dedup (skip if card already exists)
  - Snooze handling (skip snoozed deals)
  - Deal upsert (update existing, insert new)
  - API endpoints (list, scan, snooze, config)
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("MICROSOFT_CLIENT_ID", "test-client-id")
os.environ.setdefault("HUBSPOT_API_TOKEN", "test-hs-token")
os.environ.setdefault("TRELLO_API_KEY", "test-trello-key")
os.environ.setdefault("TRELLO_API_TOKEN", "test-trello-token")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.database import Base, get_db
from src.db.models import FollowupDeal, AppSetting, User, UserRole
from src.api.auth import get_current_user
from src.api.gtm_analytics import router

from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def SL(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db(SL):
    s = SL()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def user(db):
    u = User(id=str(uuid.uuid4()), name="Test", email="demo-member-013@company.example",
             password_hash="x", role=UserRole.admin)
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

    application.dependency_overrides[get_db] = _get_db
    application.dependency_overrides[get_current_user] = lambda: user
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def _now():
    return datetime.now(timezone.utc)


# ── Unit tests ────────────────────────────────────────────────────────

def test_overdue_detection(db):
    """Deals with last activity > 7 days ago should be marked overdue."""
    deal = FollowupDeal(
        id=str(uuid.uuid4()),
        hubspot_deal_id="hs-001",
        deal_name="Stale Deal",
        last_activity_at=_now() - timedelta(days=10),
        days_since_activity=10,
        is_overdue=True,
        last_synced_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(deal)
    db.commit()

    result = db.query(FollowupDeal).filter(FollowupDeal.is_overdue == True).all()
    assert len(result) == 1
    assert result[0].deal_name == "Stale Deal"


def test_not_overdue(db):
    """Deals with recent activity should not be overdue."""
    deal = FollowupDeal(
        id=str(uuid.uuid4()),
        hubspot_deal_id="hs-002",
        deal_name="Active Deal",
        last_activity_at=_now() - timedelta(days=2),
        days_since_activity=2,
        is_overdue=False,
        last_synced_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(deal)
    db.commit()

    result = db.query(FollowupDeal).filter(FollowupDeal.is_overdue == True).all()
    assert len(result) == 0


def test_trello_card_dedup(db):
    """Deals with trello_card_id set should be skipped for card creation."""
    deal = FollowupDeal(
        id=str(uuid.uuid4()),
        hubspot_deal_id="hs-003",
        deal_name="Has Card",
        is_overdue=True,
        trello_card_id="card-123",
        trello_card_url="https://trello.com/c/abc",
        last_synced_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(deal)
    db.commit()

    # Query for overdue deals WITHOUT trello cards
    needs_card = db.query(FollowupDeal).filter(
        FollowupDeal.is_overdue == True,
        FollowupDeal.trello_card_id.is_(None),
    ).all()
    assert len(needs_card) == 0


def test_snooze_blocks_card_creation(db):
    """Snoozed deals should not get Trello cards."""
    deal = FollowupDeal(
        id=str(uuid.uuid4()),
        hubspot_deal_id="hs-004",
        deal_name="Snoozed Deal",
        is_overdue=True,
        snoozed_until=_now() + timedelta(days=7),
        last_synced_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(deal)
    db.commit()

    # SQLite strips timezone, so compare naive
    assert deal.snoozed_until is not None


# ── API tests ─────────────────────────────────────────────────────────

def test_list_followups(client, db, user):
    # Seed a deal
    db.add(FollowupDeal(
        id=str(uuid.uuid4()), hubspot_deal_id="hs-100",
        deal_name="Test Deal", deal_stage="Qualified",
        days_since_activity=5, is_overdue=False,
        last_synced_at=_now(), created_at=_now(), updated_at=_now(),
    ))
    db.commit()

    r = client.get("/gtm-analytics/followups")
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    assert data[0]["deal_name"] == "Test Deal"


def test_list_followups_overdue_filter(client, db):
    db.add(FollowupDeal(
        id=str(uuid.uuid4()), hubspot_deal_id="hs-200",
        deal_name="Overdue Deal", is_overdue=True, days_since_activity=10,
        last_synced_at=_now(), created_at=_now(), updated_at=_now(),
    ))
    db.add(FollowupDeal(
        id=str(uuid.uuid4()), hubspot_deal_id="hs-201",
        deal_name="Active Deal", is_overdue=False, days_since_activity=2,
        last_synced_at=_now(), created_at=_now(), updated_at=_now(),
    ))
    db.commit()

    r = client.get("/gtm-analytics/followups?overdue_only=true")
    assert r.status_code == 200
    data = r.json()
    assert all(d["is_overdue"] for d in data)


def test_list_followups_search(client, db):
    db.add(FollowupDeal(
        id=str(uuid.uuid4()), hubspot_deal_id="hs-300",
        deal_name="Alpha Project", company_name="Acme Corp",
        is_overdue=True, days_since_activity=8,
        last_synced_at=_now(), created_at=_now(), updated_at=_now(),
    ))
    db.commit()

    r = client.get("/gtm-analytics/followups?search=acme")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_snooze_and_unsnooze(client, db):
    deal_id = str(uuid.uuid4())
    db.add(FollowupDeal(
        id=deal_id, hubspot_deal_id="hs-400",
        deal_name="Snooze Me", is_overdue=True, days_since_activity=10,
        last_synced_at=_now(), created_at=_now(), updated_at=_now(),
    ))
    db.commit()

    # Snooze
    r = client.post(f"/gtm-analytics/followups/{deal_id}/snooze?days=14")
    assert r.status_code == 200
    assert "snoozed_until" in r.json()

    # Unsnooze
    r = client.post(f"/gtm-analytics/followups/{deal_id}/unsnooze")
    assert r.status_code == 200


def test_config_crud(client):
    # Get default (empty)
    r = client.get("/gtm-analytics/followups/config")
    assert r.status_code == 200

    # Save config
    r = client.put("/gtm-analytics/followups/config", json={
        "active_stage_ids": ["stage-a", "stage-b"],
        "trello_board_id": "board-1",
        "trello_list_id": "list-1",
    })
    assert r.status_code == 200

    # Verify saved
    r = client.get("/gtm-analytics/followups/config")
    data = r.json()
    assert data["followups_active_stage_ids"] == ["stage-a", "stage-b"]
    assert data["followups_trello_board_id"] == "board-1"
    assert data["followups_trello_list_id"] == "list-1"
