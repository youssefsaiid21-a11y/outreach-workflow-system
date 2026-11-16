"""
Tests for newsletter models and schema integrity.

Uses an in-memory SQLite database to verify ORM models,
relationships, constraints, and the suppression classmethod.
"""

import pytest
import uuid
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import (
    Newsletter,
    NewsletterRecipient,
    NewsletterUnsubscribe,
    NewsletterTrack,
    NewsletterStatus,
    NewsletterRecipientStatus,
    User,
    UserRole,
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


def _make_user(uid="u-1"):
    return User(
        id=uid,
        name="Test User",
        email=f"{uid}@test.local",
        password_hash="fakehash",
        role=UserRole.admin,
    )


# ── Test 1: All tables exist with correct schema ────────────────────────────

class TestNewsletterTablesExist:
    def test_all_newsletter_tables_created(self, engine):
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        for table in ("newsletters", "newsletter_recipients", "newsletter_unsubscribes"):
            assert table in tables, f"Table '{table}' not created"

    def test_newsletters_columns(self, engine):
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("newsletters")}
        expected = {
            "id", "title", "track", "status", "owner_id",
            "subject_line", "preheader_text", "content_json",
            "html_rendered", "scheduled_at", "sent_at",
            "created_at", "updated_at",
        }
        for col in expected:
            assert col in cols, f"Column 'newsletters.{col}' missing"

    def test_newsletter_recipients_columns(self, engine):
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("newsletter_recipients")}
        expected = {
            "id", "newsletter_id", "contact_email", "contact_name",
            "contact_company", "source", "status",
            "sent_at", "opened_at", "clicked_at",
        }
        for col in expected:
            assert col in cols, f"Column 'newsletter_recipients.{col}' missing"

    def test_newsletter_unsubscribes_columns(self, engine):
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("newsletter_unsubscribes")}
        expected = {"id", "email", "unsubscribed_at", "newsletter_id", "reason"}
        for col in expected:
            assert col in cols, f"Column 'newsletter_unsubscribes.{col}' missing"


# ── Test 2: Insert draft newsletter succeeds ─────────────────────────────────

class TestInsertDraftNewsletter:
    def test_insert_draft(self, db):
        user = _make_user()
        db.add(user)
        db.flush()

        nl = Newsletter(
            id=str(uuid.uuid4()),
            title="April Update",
            track=NewsletterTrack.prospect,
            owner_id=user.id,
        )
        db.add(nl)
        db.commit()

        fetched = db.query(Newsletter).first()
        assert fetched.title == "April Update"
        assert fetched.track == NewsletterTrack.prospect
        assert fetched.status == NewsletterStatus.draft

    def test_insert_with_recipients(self, db):
        user = _make_user()
        db.add(user)
        db.flush()

        nl = Newsletter(
            id="nl-1",
            title="Client Digest",
            track=NewsletterTrack.client,
            owner_id=user.id,
        )
        db.add(nl)
        db.flush()

        recip = NewsletterRecipient(
            id=str(uuid.uuid4()),
            newsletter_id="nl-1",
            contact_email="reader@corp.local",
            contact_name="Reader One",
        )
        db.add(recip)
        db.commit()

        assert nl.recipient_count == 1
        assert nl.recipients[0].contact_email == "reader@corp.local"
        assert recip.status == NewsletterRecipientStatus.pending


# ── Test 3: is_suppressed() ──────────────────────────────────────────────────

class TestNewsletterSuppression:
    def test_suppressed_email_returns_true(self, db):
        unsub = NewsletterUnsubscribe(
            id=str(uuid.uuid4()),
            email="gone@corp.local",
            reason="manual",
        )
        db.add(unsub)
        db.commit()

        assert NewsletterUnsubscribe.is_suppressed(db, "gone@corp.local") is True

    def test_non_suppressed_email_returns_false(self, db):
        assert NewsletterUnsubscribe.is_suppressed(db, "active@corp.local") is False

    def test_duplicate_unsubscribe_rejected(self, db):
        db.add(NewsletterUnsubscribe(id="u1", email="dup@corp.local"))
        db.commit()
        db.add(NewsletterUnsubscribe(id="u2", email="dup@corp.local"))
        with pytest.raises(Exception):
            db.commit()


# ── Test 4: is_sendable property ─────────────────────────────────────────────

class TestIsSendable:
    def test_sendable_when_complete(self, db):
        user = _make_user()
        db.add(user)
        db.flush()

        nl = Newsletter(
            id="nl-s1",
            title="Ready",
            track=NewsletterTrack.prospect,
            owner_id=user.id,
            subject_line="Big News",
            content_json={"blocks": [{"type": "text", "value": "Hello"}]},
            status=NewsletterStatus.draft,
        )
        db.add(nl)
        db.commit()
        assert nl.is_sendable is True

    def test_not_sendable_missing_subject(self, db):
        user = _make_user()
        db.add(user)
        db.flush()

        nl = Newsletter(
            id="nl-s2",
            title="No Subject",
            track=NewsletterTrack.prospect,
            owner_id=user.id,
            content_json={"blocks": [{"type": "text"}]},
        )
        db.add(nl)
        db.commit()
        assert nl.is_sendable is False

    def test_not_sendable_missing_owner(self, db):
        nl = Newsletter(
            id="nl-s3",
            title="No Owner",
            track=NewsletterTrack.prospect,
            subject_line="Subject",
            content_json={"blocks": [{"type": "text"}]},
        )
        db.add(nl)
        db.commit()
        assert nl.is_sendable is False

    def test_sendable_with_empty_blocks(self, db):
        """Empty blocks list is valid — body_text content_json format is the primary path."""
        user = _make_user()
        db.add(user)
        db.flush()

        nl = Newsletter(
            id="nl-s4",
            title="Empty Blocks",
            track=NewsletterTrack.prospect,
            owner_id=user.id,
            subject_line="Subject",
            content_json={"blocks": []},
        )
        db.add(nl)
        db.commit()
        assert nl.is_sendable is True

    def test_not_sendable_when_already_sent(self, db):
        user = _make_user()
        db.add(user)
        db.flush()

        nl = Newsletter(
            id="nl-s5",
            title="Already Sent",
            track=NewsletterTrack.prospect,
            owner_id=user.id,
            subject_line="Subject",
            content_json={"blocks": [{"type": "text"}]},
            status=NewsletterStatus.sent,
        )
        db.add(nl)
        db.commit()
        assert nl.is_sendable is False

    def test_not_sendable_when_sending(self, db):
        """Once in 'sending' state, is_sendable is False — prevents re-entry."""
        user = _make_user()
        db.add(user)
        db.flush()

        nl = Newsletter(
            id="nl-s6",
            title="Sending",
            track=NewsletterTrack.client,
            owner_id=user.id,
            subject_line="Subject",
            content_json={"blocks": [{"type": "text"}]},
            status=NewsletterStatus.sending,
        )
        db.add(nl)
        db.commit()
        assert nl.is_sendable is False


# ── Test 5: All indexes present ──────────────────────────────────────────────

class TestNewsletterIndexes:
    def test_indexes_on_newsletters(self, engine):
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        idx_names = {idx["name"] for idx in inspector.get_indexes("newsletters")}
        assert "ix_newsletters_track" in idx_names
        assert "ix_newsletters_status" in idx_names
        assert "ix_newsletters_owner_id" in idx_names

    def test_indexes_on_newsletter_recipients(self, engine):
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        idx_names = {idx["name"] for idx in inspector.get_indexes("newsletter_recipients")}
        assert "ix_nl_recip_newsletter_email" in idx_names
        for idx in inspector.get_indexes("newsletter_recipients"):
            if idx["name"] == "ix_nl_recip_newsletter_email":
                assert bool(idx["unique"]) is True

    def test_indexes_on_newsletter_unsubscribes(self, engine):
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        idx_names = {idx["name"] for idx in inspector.get_indexes("newsletter_unsubscribes")}
        assert "ix_newsletter_unsubscribes_email" in idx_names or any(
            "email" in str(idx.get("column_names", [])) for idx in inspector.get_indexes("newsletter_unsubscribes")
        )
