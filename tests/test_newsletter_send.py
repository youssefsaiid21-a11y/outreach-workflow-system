"""
Session 5 tests for newsletter send engine.

Covers: pre-send suppression sweep, per-recipient failure handling,
status transitions (draft -> scheduled -> cancel -> draft -> send -> sent),
test email, schedule-in-past rejection, send-status endpoint, and
regressions from Sessions 2-4.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("NEWSLETTER_HMAC_SECRET", "test-hmac-secret-key")
os.environ.setdefault("MICROSOFT_CLIENT_ID", "fake-client-id")

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


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_user(db, uid=None, name="Test User", email=None) -> User:
    """Create a test user."""
    uid = uid or str(uuid.uuid4())
    email = email or "{}@test.local".format(uid)
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


def _make_newsletter(
    db,
    user,
    track=NewsletterTrack.prospect,
    title="Test NL",
    status=NewsletterStatus.draft,
) -> Newsletter:
    """Create a test newsletter with valid sendable content."""
    nl = Newsletter(
        id=str(uuid.uuid4()),
        title=title,
        track=track,
        owner_id=user.id,
        subject_line="Test Subject",
        status=status,
        content_json={
            "blocks": [
                {
                    "type": "text",
                    "eyebrow": "Test",
                    "headline": "Hello",
                    "body": "World",
                },
            ],
        },
    )
    db.add(nl)
    db.commit()
    return nl


def _make_recipient(
    db,
    newsletter_id: str,
    email: str,
    name: str = None,
    status=NewsletterRecipientStatus.pending,
) -> NewsletterRecipient:
    """Create a test newsletter recipient."""
    r = NewsletterRecipient(
        id=str(uuid.uuid4()),
        newsletter_id=newsletter_id,
        contact_email=email,
        contact_name=name,
        source="test",
        status=status,
    )
    db.add(r)
    db.commit()
    return r


def _build_app(SessionLocal, current_user_getter):
    """Build a FastAPI test app with newsletter routes."""
    from src.api.newsletters import router as newsletters_router
    from src.api.newsletters import unsub_public_router

    app = FastAPI()
    app.include_router(unsub_public_router, prefix="/api")
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

    app = _build_app(SessionLocal, _current_user)
    return TestClient(app)


def _mock_send_single_success(self, token_file, to_email, to_name, subject, html_body):
    """Mock _send_single that always succeeds."""
    return True


def _mock_send_single_factory(fail_emails: set):
    """Return a _send_single mock that fails for specific emails."""
    def _mock(self, token_file, to_email, to_name, subject, html_body):
        if to_email in fail_emails:
            return False
        return True
    return _mock


_original_path_exists = Path.exists


def _patched_path_exists(self):
    """Return True for outlook token files during tests."""
    if "outlook_" in str(self) and str(self).endswith(".json"):
        return True
    return _original_path_exists(self)


@pytest.fixture(autouse=False)
def mock_outlook_token():
    """Patch Path.exists so outlook token files appear to exist."""
    with patch.object(Path, "exists", _patched_path_exists):
        yield


# ── Test 1: Pre-send sweep marks newly-unsubscribed, only remaining are sent ─


class TestPreSendSweep:
    @patch(
        "src.services.newsletter_sender.NewsletterSendService._send_single",
        _mock_send_single_success,
    )
    def test_pre_send_sweep_skips_unsubscribed(self, db, SessionLocal, mock_outlook_token):
        """5 recipients, 1 unsubscribed mid-flow -> only 4 sent."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        emails = [
            "alpha@corp.local",
            "bravo@corp.local",
            "charlie@corp.local",
            "delta@corp.local",
            "echo@corp.local",
        ]
        for e in emails:
            _make_recipient(db, nl.id, e)

        # Unsubscribe charlie between populate and send
        db.add(NewsletterUnsubscribe(
            id=str(uuid.uuid4()),
            email="charlie@corp.local",
            reason="email_link",
        ))
        db.commit()

        from src.services.newsletter_sender import NewsletterSendService
        service = NewsletterSendService()
        result = service.send_newsletter(db, nl.id)

        assert result["sent"] == 4
        assert result["skipped_unsubscribed"] == 1
        assert result["failed"] == 0

        # Verify charlie is marked unsubscribed, not sent
        charlie = (
            db.query(NewsletterRecipient)
            .filter_by(contact_email="charlie@corp.local")
            .first()
        )
        db.refresh(charlie)
        assert charlie.status == NewsletterRecipientStatus.unsubscribed

        # Verify newsletter marked as sent
        db.refresh(nl)
        assert nl.status == NewsletterStatus.sent
        assert nl.sent_at is not None


# ── Test 2: Failure on recipient 3 of 5 -> others continue ──────────────────


class TestPerRecipientFailure:
    @patch(
        "src.services.newsletter_sender.NewsletterSendService._send_single",
        _mock_send_single_factory({"charlie@corp.local"}),
    )
    def test_failure_on_one_does_not_halt_batch(self, db, SessionLocal, mock_outlook_token):
        """Failure on recipient 3 of 5 -> others continue, recipient 3 marked 'failed'."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        emails = [
            "alpha@corp.local",
            "bravo@corp.local",
            "charlie@corp.local",
            "delta@corp.local",
            "echo@corp.local",
        ]
        for e in emails:
            _make_recipient(db, nl.id, e)

        from src.services.newsletter_sender import NewsletterSendService
        service = NewsletterSendService()
        result = service.send_newsletter(db, nl.id)

        assert result["sent"] == 4
        assert result["failed"] == 1

        # charlie is failed
        charlie = (
            db.query(NewsletterRecipient)
            .filter_by(contact_email="charlie@corp.local")
            .first()
        )
        db.refresh(charlie)
        assert charlie.status == NewsletterRecipientStatus.failed

        # Others are sent
        for e in ["alpha@corp.local", "bravo@corp.local", "delta@corp.local", "echo@corp.local"]:
            r = db.query(NewsletterRecipient).filter_by(contact_email=e).first()
            db.refresh(r)
            assert r.status == NewsletterRecipientStatus.sent

        # Newsletter still marked as sent (partial success)
        db.refresh(nl)
        assert nl.status == NewsletterStatus.sent


# ── Test 3: Status transitions ───────────────────────────────────────────────


class TestStatusTransitions:
    @patch(
        "src.services.newsletter_sender.NewsletterSendService._send_single",
        _mock_send_single_success,
    )
    def test_full_lifecycle(self, db, SessionLocal, mock_outlook_token):
        """draft -> scheduled -> cancel -> draft -> send -> sent; re-send rejected."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        _make_recipient(db, nl.id, "test@corp.local")

        from src.services.newsletter_sender import (
            NewsletterSendService,
            schedule_newsletter,
            cancel_schedule,
        )

        # draft -> scheduled
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        schedule_newsletter(db, nl.id, future, user)
        db.refresh(nl)
        assert nl.status == NewsletterStatus.scheduled
        assert nl.scheduled_at is not None

        # scheduled -> cancel -> draft
        cancel_schedule(db, nl.id, user)
        db.refresh(nl)
        assert nl.status == NewsletterStatus.draft
        assert nl.scheduled_at is None

        # draft -> send -> sent
        service = NewsletterSendService()
        result = service.send_newsletter(db, nl.id)
        assert result["sent"] == 1

        db.refresh(nl)
        assert nl.status == NewsletterStatus.sent

        # re-send on 'sent' newsletter rejected
        with pytest.raises(ValueError, match="only 'draft', 'scheduled', or 'sending'"):
            service.send_newsletter(db, nl.id)

    @patch(
        "src.services.newsletter_sender.NewsletterSendService._send_single",
        _mock_send_single_factory({"all-fail@corp.local"}),
    )
    def test_all_failed_sets_status_failed(self, db, SessionLocal, mock_outlook_token):
        """When ALL recipients fail, newsletter status = 'failed'."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        _make_recipient(db, nl.id, "all-fail@corp.local")

        from src.services.newsletter_sender import NewsletterSendService
        service = NewsletterSendService()
        result = service.send_newsletter(db, nl.id)

        assert result["sent"] == 0
        assert result["failed"] == 1

        db.refresh(nl)
        assert nl.status == NewsletterStatus.failed


# ── Test 4: Test email sends, newsletter status stays 'draft' ────────────────


class TestSendTestEmail:
    @patch(
        "src.services.newsletter_sender.NewsletterSendService._send_single",
        _mock_send_single_success,
    )
    def test_test_email_no_status_change(self, db, SessionLocal, mock_outlook_token):
        """Test email sends but newsletter status stays 'draft'."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        from src.services.newsletter_sender import NewsletterSendService
        service = NewsletterSendService()
        result = service.send_test_email(db, nl.id, "preview@corp.local")

        assert result["status"] == "sent"
        assert result["to"] == "preview@corp.local"

        # Newsletter is still draft
        db.refresh(nl)
        assert nl.status == NewsletterStatus.draft

    @patch(
        "src.services.newsletter_sender.NewsletterSendService._send_single",
        _mock_send_single_success,
    )
    def test_test_email_subject_has_prefix(self, db, SessionLocal, mock_outlook_token):
        """Test email subject line should have [TEST] prefix."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        calls = []

        def _capture(self, token_file, to_email, to_name, subject, html_body):
            calls.append(subject)
            return True

        from src.services.newsletter_sender import NewsletterSendService

        with patch.object(NewsletterSendService, "_send_single", _capture):
            service = NewsletterSendService()
            service.send_test_email(db, nl.id, "check@corp.local")

        assert len(calls) == 1
        assert calls[0].startswith("[TEST]")


# ── Test 5: Schedule in the past rejected ────────────────────────────────────


class TestScheduleValidation:
    def test_schedule_in_past_rejected(self, db, SessionLocal):
        """Scheduling a newsletter in the past raises ValueError."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        from src.services.newsletter_sender import schedule_newsletter

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        with pytest.raises(ValueError, match="future"):
            schedule_newsletter(db, nl.id, past, user)

    def test_schedule_in_past_rejected_via_endpoint(self, db, SessionLocal):
        """Schedule endpoint returns 400 for past datetime."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        client = _make_client(SessionLocal, user)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = client.post(
            "/api/newsletters/{}/schedule".format(nl.id),
            json={"scheduled_at": past},
        )
        assert resp.status_code == 400
        assert "future" in resp.json()["detail"].lower()


# ── Test 6: Send-status endpoint returns accurate counts ─────────────────────


class TestSendStatusEndpoint:
    def test_send_status_returns_counts(self, db, SessionLocal):
        """GET /send-status returns accurate per-status counts."""
        user = _make_user(db)
        nl = _make_newsletter(db, user, status=NewsletterStatus.sending)

        _make_recipient(db, nl.id, "a@corp.local", status=NewsletterRecipientStatus.sent)
        _make_recipient(db, nl.id, "b@corp.local", status=NewsletterRecipientStatus.sent)
        _make_recipient(db, nl.id, "c@corp.local", status=NewsletterRecipientStatus.failed)
        _make_recipient(db, nl.id, "d@corp.local", status=NewsletterRecipientStatus.pending)
        _make_recipient(db, nl.id, "e@corp.local", status=NewsletterRecipientStatus.unsubscribed)

        client = _make_client(SessionLocal, user)
        resp = client.get("/api/newsletters/{}/send-status".format(nl.id))
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "sending"
        assert data["total"] == 5
        assert data["sent"] == 2
        assert data["failed"] == 1
        assert data["pending"] == 1
        assert data["unsubscribed"] == 1


# ── Regression: Session 4 Test 1 (valid unsubscribe) ─────────────────────────


class TestSession4Regressions:
    def test_valid_unsubscribe_still_works(self, db, SessionLocal):
        """Session 4 Test 1 regression: valid HMAC token creates unsubscribe row."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        _make_recipient(db, nl.id, "regress4@corp.local")

        from src.services.newsletter_templates import generate_unsubscribe_token
        token = generate_unsubscribe_token("regress4@corp.local")

        # Build a public client (no auth)
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
        public_client = TestClient(app)

        resp = public_client.get(
            "/api/newsletters/unsubscribe?token={}&email=regress4@corp.local".format(token),
        )
        assert resp.status_code == 200
        assert "unsubscribed" in resp.text.lower()

        unsub = db.query(NewsletterUnsubscribe).filter_by(email="regress4@corp.local").first()
        assert unsub is not None


# ── Regression: Session 3 Test 2 (email compat) ─────────────────────────────


class TestSession3Regressions:
    def test_render_newsletter_produces_valid_html(self, db):
        """Session 3 Test 2 regression: render_newsletter produces DOCTYPE HTML with unsubscribe link."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)

        from src.services.newsletter_templates import render_newsletter
        html_output = render_newsletter(db, nl.id, recipient_email="compat@corp.local")

        assert "<!DOCTYPE html>" in html_output
        assert "unsubscribe" in html_output.lower()
        assert "compat%40corp.local" in html_output


# ── Regression: Session 2 Test 2 (populate dedup) ───────────────────────────


class TestSession2Regressions:
    def test_populate_dedup(self, db, SessionLocal):
        """Session 2 Test 2 regression: populate_recipients deduplicates."""
        user = _make_user(db)

        # Create prospects
        for i in range(3):
            p = Prospect(
                id=str(uuid.uuid4()),
                user_id=user.id,
                email="dedup{}@corp.local".format(i),
                first_name="Dedup{}".format(i),
                status=ProspectStatus.new,
            )
            db.add(p)
        db.commit()

        nl = _make_newsletter(db, user, NewsletterTrack.prospect)

        from src.api.newsletters import populate_recipients

        # First populate
        stats1 = populate_recipients(db, nl.id, user)
        assert stats1["added"] == 3

        # Second populate — all duplicates
        stats2 = populate_recipients(db, nl.id, user)
        assert stats2["added"] == 0
        assert stats2["skipped_duplicates"] == 3
