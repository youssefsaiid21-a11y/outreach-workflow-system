"""
Session 4 tests for newsletter unsubscribe flow.

Covers: HMAC token validation, branded confirmation/error pages,
cross-newsletter suppression, idempotent unsubscribe, admin management,
and regressions from Sessions 2 and 3.
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
os.environ.setdefault("NEWSLETTER_HMAC_SECRET", "test-hmac-secret-key")

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
from src.services.newsletter_templates import generate_unsubscribe_token, verify_unsubscribe_token


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Create an in-memory SQLite engine with all tables."""
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
    """Session factory bound to the test engine."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db(SessionLocal):
    """Provide a transactional database session for tests."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_user(db, uid=None, name="Test User", email=None) -> User:
    """Create a test user."""
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


def _make_newsletter(db, user, track=NewsletterTrack.prospect, title="Test NL") -> Newsletter:
    """Create a test newsletter."""
    nl = Newsletter(
        id=str(uuid.uuid4()),
        title=title,
        track=track,
        owner_id=user.id,
        subject_line="Test Subject",
        content_json={"blocks": [{"type": "text", "eyebrow": "Test", "headline": "Hello", "body": "World"}]},
    )
    db.add(nl)
    db.commit()
    return nl


def _make_recipient(db, newsletter_id: str, email: str, status=NewsletterRecipientStatus.pending) -> NewsletterRecipient:
    """Create a test newsletter recipient."""
    r = NewsletterRecipient(
        id=str(uuid.uuid4()),
        newsletter_id=newsletter_id,
        contact_email=email,
        source="test",
        status=status,
    )
    db.add(r)
    db.commit()
    return r


def _make_prospect(db, user, email, status=ProspectStatus.new) -> Prospect:
    """Create a test prospect."""
    p = Prospect(
        id=str(uuid.uuid4()),
        user_id=user.id,
        email=email,
        first_name="Test",
        last_name="Person",
        company="TestCorp",
        status=status,
    )
    db.add(p)
    db.commit()
    return p


def _build_full_app(SessionLocal, current_user_getter):
    """Build a FastAPI app that mirrors real routing: auth-protected + public unsub."""
    from src.api.newsletters import router as newsletters_router
    from src.api.newsletters import unsub_public_router

    app = FastAPI()

    # Public: no auth
    app.include_router(unsub_public_router, prefix="/api")

    # Auth-protected
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
    """Create a TestClient with auth overrides."""
    uid = user.id

    def _current_user():
        s = SessionLocal()
        try:
            return s.query(User).filter_by(id=uid).first()
        finally:
            s.close()

    app = _build_full_app(SessionLocal, _current_user)
    return TestClient(app)


def _make_public_client(SessionLocal):
    """Create a TestClient WITHOUT auth (for public unsubscribe endpoint)."""
    from src.api.newsletters import unsub_public_router

    app = FastAPI()
    app.include_router(unsub_public_router, prefix="/api")

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


# ── Test 1: Valid token returns confirmation page, DB row created ────────────


class TestValidUnsubscribe:
    def test_valid_token_returns_confirmation(self, db, SessionLocal):
        """Valid HMAC token returns 200 with branded confirmation HTML."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        _make_recipient(db, nl.id, "victim@corp.local")

        token = generate_unsubscribe_token("victim@corp.local")
        client = _make_public_client(SessionLocal)

        resp = client.get(f"/api/newsletters/unsubscribe?token={token}&email=victim@corp.local")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "unsubscribed" in resp.text.lower()
        assert "won't receive" in resp.text.lower()

    def test_valid_token_creates_db_row(self, db, SessionLocal):
        """Valid unsubscribe creates a NewsletterUnsubscribe row."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        _make_recipient(db, nl.id, "victim@corp.local")

        token = generate_unsubscribe_token("victim@corp.local")
        client = _make_public_client(SessionLocal)

        client.get(f"/api/newsletters/unsubscribe?token={token}&email=victim@corp.local")

        unsub = db.query(NewsletterUnsubscribe).filter_by(email="victim@corp.local").first()
        assert unsub is not None
        assert unsub.reason == "email_link"

    def test_valid_token_marks_recipient_unsubscribed(self, db, SessionLocal):
        """Valid unsubscribe marks recipient status as unsubscribed."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        _make_recipient(db, nl.id, "victim@corp.local")

        token = generate_unsubscribe_token("victim@corp.local")
        client = _make_public_client(SessionLocal)

        client.get(f"/api/newsletters/unsubscribe?token={token}&email=victim@corp.local")

        recip = db.query(NewsletterRecipient).filter_by(contact_email="victim@corp.local").first()
        db.refresh(recip)
        assert recip.status == NewsletterRecipientStatus.unsubscribed


# ── Test 2: Invalid token returns error page, NO DB row ─────────────────────


class TestInvalidToken:
    def test_invalid_token_returns_error_page(self, db, SessionLocal):
        """Bad token returns 400 error page HTML, not JSON error."""
        client = _make_public_client(SessionLocal)

        resp = client.get("/api/newsletters/unsubscribe?token=badtoken123&email=test@corp.local")
        assert resp.status_code == 400
        assert "text/html" in resp.headers["content-type"]
        assert "something went wrong" in resp.text.lower()

    def test_invalid_token_no_db_row(self, db, SessionLocal):
        """Bad token does NOT create unsubscribe row."""
        client = _make_public_client(SessionLocal)

        client.get("/api/newsletters/unsubscribe?token=badtoken123&email=test@corp.local")

        count = db.query(NewsletterUnsubscribe).count()
        assert count == 0

    def test_error_page_does_not_reveal_email(self, db, SessionLocal):
        """Error page should not contain the email address."""
        client = _make_public_client(SessionLocal)

        resp = client.get("/api/newsletters/unsubscribe?token=bad&email=secret@corp.local")
        assert "secret@corp.local" not in resp.text


# ── Test 3: Duplicate unsubscribe is idempotent ─────────────────────────────


class TestIdempotentUnsubscribe:
    def test_double_unsubscribe_only_one_row(self, db, SessionLocal):
        """Clicking unsubscribe twice creates only one DB row."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        _make_recipient(db, nl.id, "dupe@corp.local")

        token = generate_unsubscribe_token("dupe@corp.local")
        client = _make_public_client(SessionLocal)

        resp1 = client.get(f"/api/newsletters/unsubscribe?token={token}&email=dupe@corp.local")
        resp2 = client.get(f"/api/newsletters/unsubscribe?token={token}&email=dupe@corp.local")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert "unsubscribed" in resp2.text.lower()

        count = db.query(NewsletterUnsubscribe).filter_by(email="dupe@corp.local").count()
        assert count == 1


# ── Test 4: Cross-newsletter suppression ────────────────────────────────────


class TestCrossNewsletterSuppression:
    def test_unsub_from_one_removes_from_all(self, db, SessionLocal):
        """Unsubscribing via newsletter A also marks recipient in B and C."""
        user = _make_user(db)
        nl_a = _make_newsletter(db, user, title="NL-A")
        nl_b = _make_newsletter(db, user, title="NL-B")
        nl_c = _make_newsletter(db, user, title="NL-C")

        email = "crossnl@corp.local"
        _make_recipient(db, nl_a.id, email)
        _make_recipient(db, nl_b.id, email)
        _make_recipient(db, nl_c.id, email)

        token = generate_unsubscribe_token(email)
        client = _make_public_client(SessionLocal)

        client.get(f"/api/newsletters/unsubscribe?token={token}&email={email}")

        # All three should be unsubscribed
        recipients = db.query(NewsletterRecipient).filter_by(contact_email=email).all()
        for r in recipients:
            db.refresh(r)
        assert len(recipients) == 3
        assert all(r.status == NewsletterRecipientStatus.unsubscribed for r in recipients)


# ── Test 5: After unsubscribe, populate_recipients does NOT re-add ──────────


class TestPopulateRegressionAfterUnsub:
    def test_populate_skips_unsubscribed_email(self, db, SessionLocal):
        """Session 2 regression: populate_recipients must not re-add unsubscribed email."""
        user = _make_user(db)
        email = "was-unsub@corp.local"

        # Create a prospect
        _make_prospect(db, user, email, ProspectStatus.new)

        # Unsubscribe the email
        db.add(NewsletterUnsubscribe(
            id=str(uuid.uuid4()),
            email=email,
            reason="email_link",
        ))
        db.commit()

        # Create new newsletter and try to populate
        nl = _make_newsletter(db, user, NewsletterTrack.prospect)

        from src.api.newsletters import populate_recipients
        stats = populate_recipients(db, nl.id, user)

        assert stats["added"] == 0
        assert stats["skipped_unsubscribed"] == 1


# ── Test 6: Admin endpoints ─────────────────────────────────────────────────


class TestAdminUnsubscribeEndpoints:
    def test_admin_post_adds_unsubscribe(self, db, SessionLocal):
        """POST /newsletters/unsubscribes adds an unsubscribe row."""
        user = _make_user(db)
        client = _make_client(SessionLocal, user)

        resp = client.post(
            "/api/newsletters/unsubscribes",
            json={"email": "admin-added@corp.local", "reason": "requested_by_legal"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "admin-added@corp.local"
        assert data["status"] == "added"

        unsub = db.query(NewsletterUnsubscribe).filter_by(email="admin-added@corp.local").first()
        assert unsub is not None

    def test_admin_post_duplicate_409(self, db, SessionLocal):
        """POST duplicate email returns 409."""
        user = _make_user(db)
        db.add(NewsletterUnsubscribe(id=str(uuid.uuid4()), email="exists@corp.local"))
        db.commit()

        client = _make_client(SessionLocal, user)
        resp = client.post(
            "/api/newsletters/unsubscribes",
            json={"email": "exists@corp.local"},
        )
        assert resp.status_code == 409

    def test_admin_get_lists_paginated(self, db, SessionLocal):
        """GET /newsletters/unsubscribes returns paginated list."""
        user = _make_user(db)
        for i in range(5):
            db.add(NewsletterUnsubscribe(
                id=str(uuid.uuid4()),
                email=f"unsub{i:02d}@corp.local",
                reason="test",
            ))
        db.commit()

        client = _make_client(SessionLocal, user)

        resp = client.get("/api/newsletters/unsubscribes?page=1&per_page=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 3
        assert data["page"] == 1

        resp2 = client.get("/api/newsletters/unsubscribes?page=2&per_page=3")
        data2 = resp2.json()
        assert len(data2["items"]) == 2

    def test_admin_delete_removes_unsubscribe(self, db, SessionLocal):
        """DELETE /newsletters/unsubscribes/{email} removes the row."""
        user = _make_user(db)
        db.add(NewsletterUnsubscribe(id=str(uuid.uuid4()), email="resubme@corp.local", reason="test"))
        db.commit()

        client = _make_client(SessionLocal, user)
        resp = client.delete("/api/newsletters/unsubscribes/resubme@corp.local")
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"

        unsub = db.query(NewsletterUnsubscribe).filter_by(email="resubme@corp.local").first()
        assert unsub is None

    def test_admin_delete_nonexistent_404(self, db, SessionLocal):
        """DELETE nonexistent email returns 404."""
        user = _make_user(db)
        client = _make_client(SessionLocal, user)
        resp = client.delete("/api/newsletters/unsubscribes/nope@corp.local")
        assert resp.status_code == 404

    def test_admin_post_marks_recipients_unsubscribed(self, db, SessionLocal):
        """Admin POST also marks existing recipients as unsubscribed."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        _make_recipient(db, nl.id, "admin-target@corp.local")

        client = _make_client(SessionLocal, user)
        client.post(
            "/api/newsletters/unsubscribes",
            json={"email": "admin-target@corp.local", "reason": "admin"},
        )

        recip = db.query(NewsletterRecipient).filter_by(contact_email="admin-target@corp.local").first()
        db.refresh(recip)
        assert recip.status == NewsletterRecipientStatus.unsubscribed


# ── Test 7: Session 3 regression -- email template rendering ────────────────


class TestSession3Regressions:
    def test_email_template_renders(self, db):
        """Session 3 Test 2 regression: render_newsletter produces valid HTML."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        from src.services.newsletter_templates import render_newsletter
        html_output = render_newsletter(db, nl.id, recipient_email="test@corp.local")

        assert "<!DOCTYPE html>" in html_output
        assert "unsubscribe" in html_output.lower()
        assert "test%40corp.local" in html_output  # email in unsubscribe link (URL-encoded)

    def test_verify_token_roundtrip(self):
        """HMAC token generation and verification roundtrip works."""
        email = "roundtrip@corp.local"
        token = generate_unsubscribe_token(email)
        assert verify_unsubscribe_token(email, token) is True
        assert verify_unsubscribe_token(email, "wrong-token") is False
        assert verify_unsubscribe_token("other@corp.local", token) is False


# ── Test 8: Session 2 regression -- populate filters suppressed ─────────────


class TestSession2Regressions:
    def test_populate_filters_newsletter_unsub(self, db, SessionLocal):
        """Session 2 Test 1 regression: populate still filters NewsletterUnsubscribe."""
        user = _make_user(db)
        _make_prospect(db, user, "good@corp.local", ProspectStatus.new)
        _make_prospect(db, user, "unsub@corp.local", ProspectStatus.new)

        db.add(NewsletterUnsubscribe(id=str(uuid.uuid4()), email="unsub@corp.local", reason="manual"))
        db.commit()

        nl = _make_newsletter(db, user, NewsletterTrack.prospect)

        from src.api.newsletters import populate_recipients
        stats = populate_recipients(db, nl.id, user)

        assert stats["added"] == 1
        assert stats["skipped_unsubscribed"] == 1
