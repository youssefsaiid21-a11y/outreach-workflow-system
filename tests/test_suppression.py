"""
Tests for suppression and domain exclusion logic.

Uses an in-memory SQLite database so tests are fast and isolated.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import (
    ExcludedDomain,
    Prospect,
    ProspectStatus,
    UnsubscribeEntry,
)
from src.services.suppression import (
    is_domain_excluded,
    is_prospect_blocked,
    add_excluded_domain,
    remove_excluded_domain,
    filter_prospects_for_campaign,
)


@pytest.fixture
def db():
    """Create a fresh in-memory SQLite database for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def seeded_db(db):
    """DB with some test data pre-loaded."""
    # Add excluded domains
    db.add(ExcludedDomain(domain="competitor.com", category="competitor"))
    db.add(ExcludedDomain(domain="company.example", category="internal"))

    # Add unsubscribed email
    db.add(UnsubscribeEntry(email="angry@example.com", reason="manual", source="manual"))

    # Add prospects
    db.add(Prospect(id="p1", first_name="John", email="john@goodcompany.com", status=ProspectStatus.pending))
    db.add(Prospect(id="p2", first_name="Jane", email="jane@competitor.com", status=ProspectStatus.pending))
    db.add(Prospect(id="p3", first_name="Angry", email="angry@example.com", status=ProspectStatus.pending))
    db.add(Prospect(id="p4", first_name="Bob", email="bob@allowed.com", status=ProspectStatus.pending))

    db.commit()
    return db


# ── Domain exclusion ─────────────────────────────────────────────────────────

class TestDomainExclusion:
    def test_excluded_domain_detected(self, seeded_db):
        assert is_domain_excluded("sales@competitor.com", seeded_db) is True

    def test_allowed_domain_passes(self, seeded_db):
        assert is_domain_excluded("user@goodcompany.com", seeded_db) is False

    def test_internal_domain_detected(self, seeded_db):
        assert is_domain_excluded("demo-member-001@company.example", seeded_db) is True

    def test_no_at_sign_returns_false(self, seeded_db):
        assert is_domain_excluded("not-an-email", seeded_db) is False


class TestExcludedDomainCRUD:
    def test_add_and_remove(self, db):
        result = add_excluded_domain("newdomain.com", "partner", db, "Testing")
        assert result["action"] == "added"
        assert is_domain_excluded("user@newdomain.com", db) is True

        assert remove_excluded_domain("newdomain.com", db) is True
        assert is_domain_excluded("user@newdomain.com", db) is False

    def test_add_idempotent(self, db):
        add_excluded_domain("test.com", "competitor", db)
        result = add_excluded_domain("test.com", "customer", db)
        assert result["action"] == "updated"

    def test_remove_nonexistent(self, db):
        assert remove_excluded_domain("doesnotexist.com", db) is False


# ── Combined prospect blocking ───────────────────────────────────────────────

class TestProspectBlocking:
    def test_unsubscribed_email_blocked(self, seeded_db):
        reason = is_prospect_blocked("angry@example.com", seeded_db)
        assert reason == "unsubscribed"

    def test_excluded_domain_blocked(self, seeded_db):
        reason = is_prospect_blocked("anyone@competitor.com", seeded_db)
        assert reason is not None
        assert "domain_excluded" in reason

    def test_clean_email_not_blocked(self, seeded_db):
        reason = is_prospect_blocked("john@goodcompany.com", seeded_db)
        assert reason is None


# ── Campaign prospect filtering ──────────────────────────────────────────────

class TestCampaignFiltering:
    def test_filters_blocked_prospects(self, seeded_db):
        allowed, blocked = filter_prospects_for_campaign(
            ["p1", "p2", "p3", "p4"], seeded_db
        )
        # p1 (goodcompany) and p4 (allowed) should pass
        assert "p1" in allowed
        assert "p4" in allowed
        # p2 (competitor) and p3 (unsubscribed) should be blocked
        blocked_ids = {b["prospect_id"] for b in blocked}
        assert "p2" in blocked_ids
        assert "p3" in blocked_ids

    def test_empty_list_returns_empty(self, seeded_db):
        allowed, blocked = filter_prospects_for_campaign([], seeded_db)
        assert allowed == []
        assert blocked == []
