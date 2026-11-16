"""
Tests for database models and schema integrity.

Uses an in-memory SQLite database to verify ORM models,
relationships, constraints, and migrations.
"""

import pytest
import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import (
    Prospect,
    ProspectStatus,
    AudienceSegment,
    Campaign,
    CampaignStatus,
    EmailSequence,
    SequenceStatus,
    StyleProfile,
    Job,
    CreditLog,
    SendLog,
    AppSetting,
    UnsubscribeEntry,
    ExcludedDomain,
)


@pytest.fixture
def engine():
    """Create in-memory SQLite engine."""
    return create_engine("sqlite:///:memory:")


@pytest.fixture
def db(engine):
    """Create all tables and return a session."""
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ── Table creation ───────────────────────────────────────────────────────────

class TestTableCreation:
    def test_all_tables_created(self, engine):
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected = {
            "prospects", "audience_segments", "campaigns",
            "email_sequences", "style_profiles", "jobs",
            "credit_logs", "send_logs", "app_settings",
            "unsubscribes", "excluded_domains",
            "conference_inboxes", "conference_leads",
        }
        for table in expected:
            assert table in tables, f"Table '{table}' not created"


# ── Prospect model ───────────────────────────────────────────────────────────

class TestProspectModel:
    def test_create_prospect(self, db):
        p = Prospect(
            id=str(uuid.uuid4()),
            first_name="John",
            last_name="Doe",
            email="john@acme.com",
            title="VP Sales",
            company="Acme Corp",
            industry="SaaS",
            headcount=150,
            location="United States",
        )
        db.add(p)
        db.commit()

        fetched = db.query(Prospect).first()
        assert fetched.first_name == "John"
        assert fetched.email == "john@acme.com"
        assert fetched.status == ProspectStatus.new

    def test_default_status(self, db):
        p = Prospect(id="p1", first_name="Jane", email="jane@test.com")
        db.add(p)
        db.commit()
        assert p.status == ProspectStatus.new

    def test_status_transitions(self, db):
        p = Prospect(id="p1", first_name="Test", email="test@test.com")
        db.add(p)
        db.commit()

        for status in ProspectStatus:
            p.status = status
            db.commit()
            assert p.status == status


# ── Campaign model ───────────────────────────────────────────────────────────

class TestCampaignModel:
    def test_create_campaign(self, db):
        seg = AudienceSegment(
            id="seg-1", name="test-segment",
            filters={"titles": ["VP Sales"]},
        )
        db.add(seg)

        c = Campaign(
            id="camp-1",
            name="Test Campaign",
            goal="Book meetings",
            value_prop="We reduce churn by 30%",
            segment_id="seg-1",
        )
        db.add(c)
        db.commit()

        fetched = db.query(Campaign).first()
        assert fetched.name == "Test Campaign"
        assert fetched.status == CampaignStatus.draft

    def test_campaign_segment_relationship(self, db):
        seg = AudienceSegment(id="seg-1", name="test", filters={})
        c = Campaign(id="c-1", name="Camp", goal="g", value_prop="v", segment_id="seg-1")
        db.add_all([seg, c])
        db.commit()

        assert c.segment.name == "test"


# ── Email sequence model ────────────────────────────────────────────────────

class TestEmailSequenceModel:
    def test_create_sequence(self, db):
        p = Prospect(id="p1", first_name="John", email="john@test.com")
        c = Campaign(id="c1", name="Camp", goal="g", value_prop="v")
        seq = EmailSequence(
            id="seq-1",
            campaign_id="c1",
            prospect_id="p1",
            email1_subject="Subject 1",
            email1_body="Body 1",
        )
        db.add_all([p, c, seq])
        db.commit()

        fetched = db.query(EmailSequence).first()
        assert fetched.status == SequenceStatus.pending
        assert fetched.approved is False
        assert fetched.current_step == 1

    def test_sequence_campaign_relationship(self, db):
        p = Prospect(id="p1", first_name="John", email="john@test.com")
        c = Campaign(id="c1", name="Camp", goal="g", value_prop="v")
        seq = EmailSequence(id="s1", campaign_id="c1", prospect_id="p1")
        db.add_all([p, c, seq])
        db.commit()

        assert seq.campaign.name == "Camp"
        assert seq.prospect.first_name == "John"


# ── Suppression models ──────────────────────────────────────────────────────

class TestUnsubscribeModel:
    def test_add_unsubscribe(self, db):
        entry = UnsubscribeEntry(email="test@test.com", reason="manual", source="manual")
        db.add(entry)
        db.commit()

        fetched = db.query(UnsubscribeEntry).filter_by(email="test@test.com").first()
        assert fetched is not None
        assert fetched.reason == "manual"

    def test_duplicate_email_rejected(self, db):
        db.add(UnsubscribeEntry(email="dup@test.com", reason="manual"))
        db.commit()
        db.add(UnsubscribeEntry(email="dup@test.com", reason="bounce"))
        with pytest.raises(Exception):
            db.commit()


class TestExcludedDomainModel:
    def test_create(self, db):
        ed = ExcludedDomain(domain="competitor.com", category="competitor", notes="Direct competitor")
        db.add(ed)
        db.commit()

        fetched = db.query(ExcludedDomain).first()
        assert fetched.domain == "competitor.com"
        assert fetched.category == "competitor"

    def test_categories(self, db):
        for cat in ["competitor", "customer", "partner", "internal"]:
            db.add(ExcludedDomain(domain=f"{cat}.com", category=cat))
        db.commit()
        assert db.query(ExcludedDomain).count() == 4


# ── Other models ─────────────────────────────────────────────────────────────

class TestJobModel:
    def test_create_job(self, db):
        j = Job(id="j1", type="generate_sequences", status="pending", payload={"campaign_id": "c1"})
        db.add(j)
        db.commit()
        assert j.progress == 0
        assert j.status == "pending"


class TestCreditLogModel:
    def test_create(self, db):
        cl = CreditLog(id="cl1", provider="apollo", operation="reveal", credits_used=5)
        db.add(cl)
        db.commit()
        assert cl.credits_used == 5


class TestAppSettingModel:
    def test_key_value(self, db):
        setting = AppSetting(key="custom_instructions", value="Be concise")
        db.add(setting)
        db.commit()

        fetched = db.query(AppSetting).filter_by(key="custom_instructions").first()
        assert fetched.value == "Be concise"

    def test_update(self, db):
        setting = AppSetting(key="test_key", value="v1")
        db.add(setting)
        db.commit()
        setting.value = "v2"
        db.commit()
        fetched = db.query(AppSetting).filter_by(key="test_key").first()
        assert fetched.value == "v2"
