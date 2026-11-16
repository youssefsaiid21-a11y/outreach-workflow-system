"""
Tests for the inbound lead poller's _register_inbound_leads function.

Covers:
  - External senders are registered as inbound leads
  - Internal domain senders are skipped
  - Drafts are skipped
  - Duplicate senders (same email + month) are de-duplicated
  - Month is extracted from received_at
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

from src.db.database import Base
from src.db.models import GtmManualEntry
from src.services.inbound_lead_poller import _register_inbound_leads, INTERNAL_DOMAINS


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
def db(engine):
    SL = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SL()
    try:
        yield session
    finally:
        session.close()


def _msg(from_email, subject="Hello", received_at="2026-03-15T10:00:00Z", is_draft=False):
    return {
        "id": str(uuid.uuid4()),
        "conversation_id": "",
        "from_email": from_email,
        "from_name": from_email.split("@")[0],
        "subject": subject,
        "body_preview": "Some preview text",
        "received_at": received_at,
        "is_draft": is_draft,
    }


def test_registers_external_sender(db):
    messages = [_msg("prospect@external.com", "Inquiry about products")]
    count = _register_inbound_leads(db, messages)
    assert count == 1

    entries = db.query(GtmManualEntry).all()
    assert len(entries) == 1
    assert entries[0].category == "inbound"
    assert entries[0].metric_key == "inbound_lead"
    assert entries[0].source_ref == "prospect@external.com"
    assert entries[0].month == "2026-03"
    assert "Inquiry about products" in entries[0].note


def test_skips_internal_domain(db):
    messages = [_msg("demo-member-004@company.example")]
    count = _register_inbound_leads(db, messages)
    assert count == 0
    assert db.query(GtmManualEntry).count() == 0


def test_skips_drafts(db):
    messages = [_msg("prospect@external.com", is_draft=True)]
    count = _register_inbound_leads(db, messages)
    assert count == 0


def test_dedup_same_sender_same_month(db):
    messages = [
        _msg("prospect@external.com", "First email", "2026-03-10T10:00:00Z"),
        _msg("prospect@external.com", "Second email", "2026-03-20T10:00:00Z"),
    ]
    count = _register_inbound_leads(db, messages)
    assert count == 1  # Only one entry for the first message

    # Run again — should not create a duplicate
    count2 = _register_inbound_leads(db, messages)
    assert count2 == 0
    assert db.query(GtmManualEntry).count() == 1


def test_different_months_creates_separate_entries(db):
    messages = [
        _msg("prospect@external.com", "March email", "2026-03-15T10:00:00Z"),
        _msg("prospect@external.com", "April email", "2026-04-15T10:00:00Z"),
    ]
    count = _register_inbound_leads(db, messages)
    assert count == 2
    entries = db.query(GtmManualEntry).order_by(GtmManualEntry.month).all()
    assert entries[0].month == "2026-03"
    assert entries[1].month == "2026-04"


def test_multiple_external_senders(db):
    messages = [
        _msg("alice@external.com", "Question"),
        _msg("bob@another.com", "Interested"),
        _msg("demo-member-006@company.example", "Ignore me"),
    ]
    count = _register_inbound_leads(db, messages)
    assert count == 2  # alice + bob, not internal


def test_empty_messages(db):
    count = _register_inbound_leads(db, [])
    assert count == 0


def test_internal_domains_configurable():
    # Default internal domains should include invigilo.ai and company.example
    assert "invigilo.ai" in INTERNAL_DOMAINS
    assert "company.example" in INTERNAL_DOMAINS
