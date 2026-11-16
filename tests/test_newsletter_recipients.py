"""
Session 2 tests for newsletter recipient management.

Covers: populate from Prospect table, manual add/remove, CSV import,
suppression enforcement, pagination, and regressions from Session 1.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.database import Base, get_db
from src.db.models import (
    Newsletter,
    NewsletterRecipient,
    NewsletterRecipientStatus,
    NewsletterStatus,
    NewsletterTrack,
    NewsletterUnsubscribe,
    Prospect,
    ProspectStatus,
    UnsubscribeEntry,
    User,
    UserRole,
)
from src.api.auth import get_current_user


# ── Fixtures ─────────────────────────────────────────────────────────────────


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
def SessionLocal(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db(SessionLocal):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_user(db, uid=None, name="Test User", email=None):
    uid = uid or str(uuid.uuid4())
    email = email or f"{uid}@test.local"
    u = User(
        id=uid,
        name=name,
        email=email,
        password_hash="fakehash",
        role=UserRole.admin,
    )
    db.add(u)
    db.commit()
    return u


def _make_prospect(db, user, email, status=ProspectStatus.new, first_name="Test", last_name="Person", company="TestCorp"):
    p = Prospect(
        id=str(uuid.uuid4()),
        user_id=user.id,
        email=email,
        first_name=first_name,
        last_name=last_name,
        company=company,
        status=status,
    )
    db.add(p)
    db.commit()
    return p


def _make_newsletter(db, user, track=NewsletterTrack.prospect, title="Test NL"):
    nl = Newsletter(
        id=str(uuid.uuid4()),
        title=title,
        track=track,
        owner_id=user.id,
    )
    db.add(nl)
    db.commit()
    return nl


def _build_app(SessionLocal, current_user_getter):
    from src.api.newsletters import router as newsletters_router

    app = FastAPI()
    app.include_router(newsletters_router, prefix="/api")

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = current_user_getter
    return app


def _make_client(SessionLocal, user):
    uid = user.id

    def _current_user():
        s = SessionLocal()
        try:
            return s.query(User).filter_by(id=uid).first()
        finally:
            s.close()

    app = _build_app(SessionLocal, _current_user)
    return TestClient(app)


# ── Test 1: populate_recipients filters out unsubscribed emails ──────────────


class TestPopulateFiltersUnsubscribed:
    def test_newsletter_unsub_filtered(self, db, SessionLocal):
        """Emails in NewsletterUnsubscribe are skipped during populate."""
        user = _make_user(db)
        _make_prospect(db, user, "good@corp.local", ProspectStatus.new)
        _make_prospect(db, user, "unsub-nl@corp.local", ProspectStatus.new)

        # Suppress via newsletter unsubscribe
        db.add(NewsletterUnsubscribe(id=str(uuid.uuid4()), email="unsub-nl@corp.local", reason="manual"))
        db.commit()

        nl = _make_newsletter(db, user, NewsletterTrack.prospect)

        from src.api.newsletters import populate_recipients
        stats = populate_recipients(db, nl.id, user)

        assert stats["added"] == 1
        assert stats["skipped_unsubscribed"] == 1

    def test_campaign_unsub_filtered(self, db, SessionLocal):
        """Emails in UnsubscribeEntry (campaign suppression) are also skipped."""
        user = _make_user(db)
        _make_prospect(db, user, "good@corp.local", ProspectStatus.new)
        _make_prospect(db, user, "unsub-camp@corp.local", ProspectStatus.new)

        # Suppress via campaign unsubscribe
        db.add(UnsubscribeEntry(email="unsub-camp@corp.local", reason="bounce"))
        db.commit()

        nl = _make_newsletter(db, user, NewsletterTrack.prospect)

        from src.api.newsletters import populate_recipients
        stats = populate_recipients(db, nl.id, user)

        assert stats["added"] == 1
        assert stats["skipped_unsubscribed"] == 1

    def test_both_suppression_sources(self, db, SessionLocal):
        """Both suppression lists are checked."""
        user = _make_user(db)
        _make_prospect(db, user, "ok@corp.local", ProspectStatus.new)
        _make_prospect(db, user, "nl-gone@corp.local", ProspectStatus.new)
        _make_prospect(db, user, "camp-gone@corp.local", ProspectStatus.new)

        db.add(NewsletterUnsubscribe(id=str(uuid.uuid4()), email="nl-gone@corp.local"))
        db.add(UnsubscribeEntry(email="camp-gone@corp.local", reason="manual"))
        db.commit()

        nl = _make_newsletter(db, user, NewsletterTrack.prospect)

        from src.api.newsletters import populate_recipients
        stats = populate_recipients(db, nl.id, user)

        assert stats["added"] == 1
        assert stats["skipped_unsubscribed"] == 2


# ── Test 2: populate returns correct stats + dedup on re-run ─────────────────


class TestPopulateStatsAndDedup:
    def test_first_populate(self, db, SessionLocal):
        user = _make_user(db)
        for i in range(5):
            _make_prospect(db, user, f"p{i}@corp.local", ProspectStatus.new)

        nl = _make_newsletter(db, user, NewsletterTrack.prospect)

        from src.api.newsletters import populate_recipients
        stats = populate_recipients(db, nl.id, user)

        assert stats["added"] == 5
        assert stats["skipped_duplicates"] == 0
        assert stats["total"] == 5

    def test_re_populate_dedup(self, db, SessionLocal):
        user = _make_user(db)
        for i in range(3):
            _make_prospect(db, user, f"p{i}@corp.local", ProspectStatus.new)

        nl = _make_newsletter(db, user, NewsletterTrack.prospect)

        from src.api.newsletters import populate_recipients

        stats1 = populate_recipients(db, nl.id, user)
        assert stats1["added"] == 3

        # Add one more prospect then re-populate
        _make_prospect(db, user, "new@corp.local", ProspectStatus.new)

        stats2 = populate_recipients(db, nl.id, user)
        assert stats2["added"] == 1
        assert stats2["skipped_duplicates"] == 3
        assert stats2["total"] == 4

    def test_client_track_filters(self, db, SessionLocal):
        """Client track only picks up prospects with status=replied."""
        user = _make_user(db)
        _make_prospect(db, user, "new@corp.local", ProspectStatus.new)
        _make_prospect(db, user, "replied@corp.local", ProspectStatus.replied)
        _make_prospect(db, user, "bounced@corp.local", ProspectStatus.bounced)

        nl = _make_newsletter(db, user, NewsletterTrack.client)

        from src.api.newsletters import populate_recipients
        stats = populate_recipients(db, nl.id, user)

        assert stats["added"] == 1
        assert stats["total"] == 1


# ── Test 3: add_recipient rejects suppressed email with 409 ──────────────────


class TestAddRecipientSuppression:
    def test_reject_newsletter_unsub(self, db, SessionLocal):
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        db.add(NewsletterUnsubscribe(id=str(uuid.uuid4()), email="gone@corp.local", reason="manual"))
        db.commit()

        client = _make_client(SessionLocal, user)
        resp = client.post(
            f"/api/newsletters/{nl.id}/recipients",
            json={"email": "gone@corp.local", "name": "Gone User"},
        )
        assert resp.status_code == 409
        assert "suppressed" in resp.json()["detail"].lower()

    def test_reject_campaign_unsub(self, db, SessionLocal):
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        db.add(UnsubscribeEntry(email="camp-gone@corp.local", reason="manual"))
        db.commit()

        client = _make_client(SessionLocal, user)
        resp = client.post(
            f"/api/newsletters/{nl.id}/recipients",
            json={"email": "camp-gone@corp.local"},
        )
        assert resp.status_code == 409

    def test_add_valid_email(self, db, SessionLocal):
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        client = _make_client(SessionLocal, user)
        resp = client.post(
            f"/api/newsletters/{nl.id}/recipients",
            json={"email": "ok@corp.local", "name": "New Person", "company": "SomeCo"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["contact_email"] == "ok@corp.local"
        assert data["status"] == "added"


# ── Test 4: POST populate endpoint returns 200 with stats ────────────────────


class TestPopulateEndpoint:
    def test_populate_via_http(self, db, SessionLocal):
        user = _make_user(db)
        for i in range(3):
            _make_prospect(db, user, f"e{i}@corp.local", ProspectStatus.enrolled)

        nl = _make_newsletter(db, user, NewsletterTrack.prospect)

        client = _make_client(SessionLocal, user)
        resp = client.post(f"/api/newsletters/{nl.id}/recipients/populate")
        assert resp.status_code == 200

        data = resp.json()
        assert data["added"] == 3
        assert data["total"] == 3

    def test_populate_wrong_owner_404(self, db, SessionLocal):
        owner = _make_user(db, uid="owner-1", email="owner@test.local")
        other = _make_user(db, uid="other-1", email="other@test.local")
        nl = _make_newsletter(db, owner)

        client = _make_client(SessionLocal, other)
        resp = client.post(f"/api/newsletters/{nl.id}/recipients/populate")
        assert resp.status_code == 404


# ── Test 5: GET recipients paginates correctly ───────────────────────────────


class TestPagination:
    def test_pagination_default(self, db, SessionLocal):
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        # Create 30 recipients
        for i in range(30):
            db.add(
                NewsletterRecipient(
                    id=str(uuid.uuid4()),
                    newsletter_id=nl.id,
                    contact_email=f"r{i:03d}@corp.local",
                    source="test",
                )
            )
        db.commit()

        client = _make_client(SessionLocal, user)

        # Page 1: 25 items
        resp1 = client.get(f"/api/newsletters/{nl.id}/recipients?page=1&per_page=25")
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert len(data1["items"]) == 25
        assert data1["total"] == 30
        assert data1["page"] == 1

        # Page 2: 5 items
        resp2 = client.get(f"/api/newsletters/{nl.id}/recipients?page=2&per_page=25")
        data2 = resp2.json()
        assert len(data2["items"]) == 5
        assert data2["page"] == 2

    def test_pagination_max_100(self, db, SessionLocal):
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        client = _make_client(SessionLocal, user)

        resp = client.get(f"/api/newsletters/{nl.id}/recipients?per_page=200")
        assert resp.status_code == 422  # validation error, max is 100


# ── Test 6: Session 1 regressions ────────────────────────────────────────────


class TestSession1Regressions:
    """Re-verify Session 1 Test 2 (insert draft) + Test 3 (is_suppressed)."""

    def test_insert_draft_newsletter(self, db):
        """Session 1 Test 2: insert draft newsletter succeeds."""
        user = _make_user(db)
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

    def test_is_suppressed_positive(self, db):
        """Session 1 Test 3: is_suppressed returns True for unsubscribed."""
        unsub = NewsletterUnsubscribe(
            id=str(uuid.uuid4()),
            email="gone@corp.local",
            reason="manual",
        )
        db.add(unsub)
        db.commit()

        assert NewsletterUnsubscribe.is_suppressed(db, "gone@corp.local") is True

    def test_is_suppressed_negative(self, db):
        """Session 1 Test 3: is_suppressed returns False for non-suppressed."""
        assert NewsletterUnsubscribe.is_suppressed(db, "active@corp.local") is False


# ── Test 7: CSV import ───────────────────────────────────────────────────────


class TestCSVImport:
    def test_csv_import_with_suppression(self, db, SessionLocal):
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        db.add(NewsletterUnsubscribe(id=str(uuid.uuid4()), email="blocked@corp.local"))
        db.commit()

        csv_content = "email,name,company\nok1@corp.local,Alice,ACo\nblocked@corp.local,Bob,BCo\nok2@corp.local,Carol,CCo\n"

        from src.api.newsletters import import_csv_recipients
        stats = import_csv_recipients(db, nl.id, csv_content, user)

        assert stats["added"] == 2
        assert stats["skipped_unsubscribed"] == 1
        assert stats["total"] == 3

    def test_csv_import_dedup(self, db, SessionLocal):
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        csv1 = "email,name,company\na@corp.local,A,Co\n"
        csv2 = "email,name,company\na@corp.local,A,Co\nb@corp.local,B,Co\n"

        from src.api.newsletters import import_csv_recipients
        stats1 = import_csv_recipients(db, nl.id, csv1, user)
        assert stats1["added"] == 1

        stats2 = import_csv_recipients(db, nl.id, csv2, user)
        assert stats2["added"] == 1
        assert stats2["skipped_duplicates"] == 1


# ── Test 8: DELETE recipient ─────────────────────────────────────────────────


class TestRemoveRecipient:
    def test_remove_existing(self, db, SessionLocal):
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        db.add(NewsletterRecipient(
            id=str(uuid.uuid4()),
            newsletter_id=nl.id,
            contact_email="remove-me@corp.local",
        ))
        db.commit()

        client = _make_client(SessionLocal, user)
        resp = client.delete(f"/api/newsletters/{nl.id}/recipients/remove-me@corp.local")
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"

    def test_remove_nonexistent_404(self, db, SessionLocal):
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        client = _make_client(SessionLocal, user)
        resp = client.delete(f"/api/newsletters/{nl.id}/recipients/nope@corp.local")
        assert resp.status_code == 404
