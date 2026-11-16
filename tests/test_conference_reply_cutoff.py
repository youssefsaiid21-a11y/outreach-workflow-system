"""
Regression tests for the conference reply detection + sequence cutoff
feature (Events-reply-cutoff 2026-04-09).

Three layers of coverage:

1) ``_match_and_mark`` - pure match logic against fabricated inbox
   message dicts. No Graph, no HTTP, just in-memory DB + the function.
   This is where the match rules are pinned.

2) ``routes_drafts`` guards - integration tests via FastAPI TestClient
   hitting the draft endpoints with a replied lead and asserting the
   request is refused with 409 (single-lead) or silently skipped
   (bulk).

3) ``routes_replies`` - the new /check-replies and /clear-reply
   endpoints, including the false-positive undo path.

The fixture style mirrors the existing test_conference_routes.py:
SQLite + StaticPool + manual User/ConferenceLead seeding. The
Outlook client is monkey-patched so tests never touch Graph.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("MICROSOFT_CLIENT_ID", "test-client-id")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.database import Base, get_db
from src.db.models import ConferenceLead, User, UserRole
from src.api.auth import get_current_user
from src.api.conference import router as conference_router
from src.services.conference_reply_poller import _match_and_mark, clear_last_poll_cursor


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
def SessionLocal(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db(SessionLocal):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_user(db, name: str, email: str) -> User:
    u = User(
        id=str(uuid.uuid4()),
        name=name,
        email=email,
        password_hash="unused",
        role=UserRole.rep,
    )
    db.add(u)
    db.commit()
    return u


def _make_lead(
    db,
    user: User,
    *,
    email: str,
    first_name: str = "Lead",
    last_name: str = "One",
    company: str = "Acme",
    conference_name: str = "ADIPEC2026",
    generated_subject: str = "Great meeting at ADIPEC",
    generated_body: str = "Hi there,\n\nGood chat.\n\nTalk soon,",
    draft_created_at: datetime | None = None,
    draft_message_id: str | None = None,
    replied_at: datetime | None = None,
) -> ConferenceLead:
    """Create a ConferenceLead with defaults that make it eligible
    for draft push (generated + drafted) unless overridden."""
    lead = ConferenceLead(
        id=str(uuid.uuid4()),
        conference_name=conference_name,
        first_name=first_name,
        last_name=last_name,
        email=email,
        company=company,
        user_id=user.id,
        generated_subject=generated_subject,
        generated_body=generated_body,
        generated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        draft_created_at=draft_created_at,
        draft_message_id=draft_message_id,
        replied_at=replied_at,
    )
    db.add(lead)
    db.commit()
    return lead


def _make_msg(
    *,
    from_email: str,
    received_at: str,
    subject: str = "Re: Great meeting",
    body_preview: str = "Sounds good. Let's set up a call.",
    is_draft: bool = False,
    conversation_id: str = "",
) -> dict:
    return {
        "id": "m_" + uuid.uuid4().hex[:8],
        "conversation_id": conversation_id or ("c_" + uuid.uuid4().hex[:8]),
        "from_email": from_email,
        "from_name": "Prospect",
        "subject": subject,
        "body_preview": body_preview,
        "received_at": received_at,
        "is_draft": is_draft,
    }


def _build_app(SessionLocal, current_user_getter):
    """Mount the conference router on a fresh app with overridden deps."""
    app = FastAPI()
    app.include_router(conference_router, prefix="/api")

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = current_user_getter
    return app


# ── Layer 1: _match_and_mark pure logic ───────────────────────────────


class TestMatchAndMark:
    """Pin the match rules. Each test is a single rule in isolation."""

    def test_empty_messages_no_marks(self, db):
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
        )
        marked = _match_and_mark(db, user, messages=[])
        assert marked == 0
        db.refresh(lead)
        assert lead.replied_at is None

    def test_reply_from_matching_email_marks_lead(self, db):
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
        )
        messages = [
            _make_msg(
                from_email="prospect@acme.com",
                received_at="2026-04-08T14:00:00Z",
                subject="Re: Great meeting at ADIPEC",
                body_preview="Happy to chat - Thursday works.",
            ),
        ]
        marked = _match_and_mark(db, user, messages)
        assert marked == 1
        db.refresh(lead)
        assert lead.replied_at is not None
        assert lead.reply_subject == "Re: Great meeting at ADIPEC"
        assert lead.reply_snippet == "Happy to chat - Thursday works."
        assert lead.reply_from_email == "prospect@acme.com"

    def test_sender_email_is_case_insensitive(self, db):
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="Prospect@Acme.COM",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
        )
        # Sender arrives lowercased from OutlookClient.fetch_inbox_messages
        # but let's pin the behaviour anyway.
        messages = [_make_msg(from_email="prospect@acme.com", received_at="2026-04-08T14:00:00Z")]
        marked = _match_and_mark(db, user, messages)
        assert marked == 1

    def test_reply_from_non_lead_is_ignored(self, db):
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
        )
        messages = [_make_msg(from_email="random@other.com", received_at="2026-04-08T14:00:00Z")]
        marked = _match_and_mark(db, user, messages)
        assert marked == 0

    def test_reply_arriving_before_draft_is_ignored(self, db):
        """A message from the prospect that pre-dates our draft must
        not count - that's an earlier unrelated thread from the same
        address. This is the main false-positive guard."""
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
        )
        messages = [
            _make_msg(
                from_email="prospect@acme.com",
                received_at="2026-04-07T09:00:00Z",  # day BEFORE draft
            ),
        ]
        marked = _match_and_mark(db, user, messages)
        assert marked == 0
        db.refresh(lead)
        assert lead.replied_at is None

    def test_draft_messages_in_the_inbox_are_skipped(self, db):
        """Graph sometimes surfaces drafts even in inbox queries; skip."""
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
        )
        messages = [
            _make_msg(
                from_email="prospect@acme.com",
                received_at="2026-04-08T14:00:00Z",
                is_draft=True,
            ),
        ]
        marked = _match_and_mark(db, user, messages)
        assert marked == 0
        db.refresh(lead)
        assert lead.replied_at is None

    def test_self_reply_is_ignored(self, db):
        """If the user's own email address appears as the sender (self
        CC / reply-all loops), do not mark the lead as replied."""
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
        )
        messages = [
            _make_msg(from_email="demo-member-008@company.example", received_at="2026-04-08T14:00:00Z"),
        ]
        marked = _match_and_mark(db, user, messages)
        assert marked == 0

    def test_already_replied_lead_is_not_remarked(self, db):
        """Idempotency: polling twice must not re-mark a lead."""
        prior = datetime(2026, 4, 8, 12, 0)
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
            replied_at=prior,
        )
        messages = [_make_msg(from_email="prospect@acme.com", received_at="2026-04-08T14:00:00Z")]
        marked = _match_and_mark(db, user, messages)
        assert marked == 0
        db.refresh(lead)
        # replied_at didn't get clobbered
        assert lead.replied_at == prior

    def test_lead_without_draft_is_not_marked(self, db):
        """A lead that only has a generated email but never got
        drafted should not be eligible - there's no outreach to reply to."""
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=None,
        )
        messages = [_make_msg(from_email="prospect@acme.com", received_at="2026-04-08T14:00:00Z")]
        marked = _match_and_mark(db, user, messages)
        assert marked == 0

    def test_cross_user_isolation(self, db):
        """If user A and user B both have a lead with the same email,
        a reply polled by user A must NOT touch user B's lead."""
        user_a = _make_user(db, "Nehal", "demo-member-008@company.example")
        user_b = _make_user(db, "Mark", "demo-member-007@company.example")
        lead_a = _make_lead(db, user_a, email="prospect@acme.com", draft_created_at=datetime(2026, 4, 8, 10, 0))
        lead_b = _make_lead(
            db, user_b, email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
            conference_name="OTHER2026",  # avoid unique constraint
        )
        messages = [_make_msg(from_email="prospect@acme.com", received_at="2026-04-08T14:00:00Z")]
        # Poll as user A only
        marked = _match_and_mark(db, user_a, messages)
        assert marked == 1
        db.refresh(lead_a)
        db.refresh(lead_b)
        assert lead_a.replied_at is not None
        assert lead_b.replied_at is None

    def test_two_leads_same_email_marks_all_eligible(self, db):
        """SWAT-H9: Two leads for the same prospect email in two different
        conferences — both should be marked replied if their draft pre-dates
        the reply, preventing orphaned sequences from continuing outreach."""
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        old = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 3, 15, 10, 0),
            conference_name="OLD2026",
        )
        fresh = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
            conference_name="FRESH2026",
        )
        messages = [_make_msg(from_email="prospect@acme.com", received_at="2026-04-08T14:00:00Z")]
        marked = _match_and_mark(db, user, messages)
        assert marked == 2
        db.refresh(old)
        db.refresh(fresh)
        assert fresh.replied_at is not None
        assert old.replied_at is not None

    def test_reply_body_preview_is_truncated(self, db):
        """Safety: a ridiculously long body_preview shouldn't blow up
        the column."""
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
        )
        huge = "x" * 5000
        messages = [
            _make_msg(
                from_email="prospect@acme.com",
                received_at="2026-04-08T14:00:00Z",
                body_preview=huge,
                subject=huge,
            ),
        ]
        marked = _match_and_mark(db, user, messages)
        assert marked == 1
        db.refresh(lead)
        assert len(lead.reply_snippet) <= 2000
        assert len(lead.reply_subject) <= 2000


# ── Layer 2: route guards ─────────────────────────────────────────────


class TestDraftGuards:
    """The draft endpoints must refuse to push further outreach for a
    replied lead. The contract:

    - POST /conference/draft/{id}           → 409
    - POST /conference/draft-sequence/{id}  → 409
    - POST /conference/draft-all            → silently skips replied leads
    - POST /conference/generate             → 409
    - POST /conference/generate-sequence    → 409
    """

    def _client_for(self, SessionLocal, user):
        app = _build_app(SessionLocal, lambda: user)
        return TestClient(app)

    def test_single_draft_refused_with_409(self, db, SessionLocal):
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
            replied_at=datetime(2026, 4, 8, 14, 0),
        )
        client = self._client_for(SessionLocal, user)
        resp = client.post(f"/api/conference/draft/{lead.id}")
        assert resp.status_code == 409
        assert "replied" in resp.json()["detail"].lower()

    def test_single_draft_mentions_clear_reply_undo_path(self, db, SessionLocal):
        """The 409 body must tell the operator HOW to override - i.e.
        the clear-reply endpoint path - so support doesn't get paged
        for an out-of-office false positive."""
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
            replied_at=datetime(2026, 4, 8, 14, 0),
        )
        client = self._client_for(SessionLocal, user)
        resp = client.post(f"/api/conference/draft/{lead.id}")
        assert resp.status_code == 409
        assert "clear-reply" in resp.json()["detail"]

    def test_draft_sequence_step_refused_with_409(self, db, SessionLocal):
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
            replied_at=datetime(2026, 4, 8, 14, 0),
        )
        import json as _json
        lead.generated_sequence = _json.dumps({
            "email1": {"subject": "s1", "body": "b1"},
            "email2": {"subject": "s2", "body": "b2"},
        })
        db.commit()
        client = self._client_for(SessionLocal, user)
        resp = client.post(f"/api/conference/draft-sequence/{lead.id}?step=email2")
        assert resp.status_code == 409
        assert "replied" in resp.json()["detail"].lower()

    def test_draft_all_silently_skips_replied(self, db, SessionLocal):
        """draft-all is a sweep - it should just exclude replied leads
        from its query, not refuse the whole batch."""
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        _make_lead(
            db, user,
            email="a@acme.com",
            conference_name="C1",
            draft_created_at=None,  # eligible
        )
        _make_lead(
            db, user,
            email="b@acme.com",
            conference_name="C1",
            replied_at=datetime(2026, 4, 8, 14, 0),
        )
        client = self._client_for(SessionLocal, user)
        # We patch _dispatch_draft so no real Outlook call fires.
        with patch("src.api.conference.routes_drafts._dispatch_draft") as mock_dispatch:
            mock_dispatch.return_value = {"message_id": "fake"}
            resp = client.post("/api/conference/draft-all?conference_name=C1")
        assert resp.status_code == 200
        data = resp.json()
        # Only the non-replied, non-yet-drafted lead should have been dispatched.
        # lead b was replied → excluded from the query entirely
        assert mock_dispatch.call_count == 1
        assert data["drafted"] == 1

    def test_generate_single_refused_with_409(self, db, SessionLocal):
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
            replied_at=datetime(2026, 4, 8, 14, 0),
        )
        client = self._client_for(SessionLocal, user)
        resp = client.post("/api/conference/generate", json={"lead_id": lead.id, "context": "test"})
        assert resp.status_code == 409

    def test_generate_sequence_refused_with_409(self, db, SessionLocal):
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
            replied_at=datetime(2026, 4, 8, 14, 0),
        )
        client = self._client_for(SessionLocal, user)
        resp = client.post("/api/conference/generate-sequence", json={"lead_id": lead.id, "context": "test"})
        assert resp.status_code == 409


# ── Layer 3: /check-replies and /clear-reply routes ──────────────────


class TestReplyRoutes:

    def test_check_replies_triggers_poll_for_current_user(self, db, SessionLocal):
        """POST /conference/check-replies must call the poller for the
        current user and return the count of newly-marked leads."""
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
        )
        app = _build_app(SessionLocal, lambda: user)
        client = TestClient(app)

        # Patch the poller so no Graph calls happen.
        with patch("src.services.conference_reply_poller.poll_replies_for_user") as mock_poll:
            mock_poll.return_value = 3
            resp = client.post("/api/conference/check-replies")

        assert resp.status_code == 200
        assert resp.json() == {"marked": 3}
        mock_poll.assert_called_once()
        # First positional arg is a db session; second is the user.
        args, _kwargs = mock_poll.call_args
        assert args[1].id == user.id

    def test_check_replies_surfaces_outlook_not_connected_as_400(self, db, SessionLocal):
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        app = _build_app(SessionLocal, lambda: user)
        client = TestClient(app)

        with patch("src.services.conference_reply_poller.poll_replies_for_user") as mock_poll:
            mock_poll.side_effect = ValueError("Outlook is not connected for this user.")
            resp = client.post("/api/conference/check-replies")

        assert resp.status_code == 400
        assert "not connected" in resp.json()["detail"].lower()

    def test_clear_reply_undoes_flag(self, db, SessionLocal):
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
            replied_at=datetime(2026, 4, 8, 14, 0),
        )
        lead.reply_subject = "Out of Office"
        lead.reply_snippet = "I'll be back Monday..."
        lead.reply_from_email = "prospect@acme.com"
        db.commit()

        app = _build_app(SessionLocal, lambda: user)
        client = TestClient(app)
        resp = client.post(f"/api/conference/leads/{lead.id}/clear-reply")
        assert resp.status_code == 200
        data = resp.json()
        assert data["replied_at"] is None
        assert data["reply_subject"] is None
        assert data["reply_snippet"] is None
        assert data["reply_from_email"] is None

        db.refresh(lead)
        assert lead.replied_at is None

    def test_clear_reply_idempotent_on_non_replied_lead(self, db, SessionLocal):
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
        )
        app = _build_app(SessionLocal, lambda: user)
        client = TestClient(app)
        resp = client.post(f"/api/conference/leads/{lead.id}/clear-reply")
        assert resp.status_code == 200
        assert resp.json()["replied_at"] is None

    def test_draft_succeeds_after_clear_reply(self, db, SessionLocal):
        """End-to-end undo: a replied lead, after clear-reply, can be
        drafted again through the normal /draft endpoint."""
        user = _make_user(db, "Nehal", "demo-member-008@company.example")
        lead = _make_lead(
            db, user,
            email="prospect@acme.com",
            draft_created_at=None,  # not yet drafted
            replied_at=datetime(2026, 4, 8, 14, 0),
        )
        app = _build_app(SessionLocal, lambda: user)
        client = TestClient(app)

        # First attempt must be refused with 409
        resp1 = client.post(f"/api/conference/draft/{lead.id}")
        assert resp1.status_code == 409

        # Clear the reply
        resp_clear = client.post(f"/api/conference/leads/{lead.id}/clear-reply")
        assert resp_clear.status_code == 200

        # Now drafting should proceed to dispatch (we mock it so no Graph call)
        with patch("src.api.conference.routes_drafts._dispatch_draft") as mock_dispatch:
            mock_dispatch.return_value = {"message_id": "fake-msg-id"}
            resp2 = client.post(f"/api/conference/draft/{lead.id}")
        assert resp2.status_code == 200
        mock_dispatch.assert_called_once()


# ── Cross-user ownership safety on clear-reply ─────────────────────────


class TestOwnership:

    def test_clear_reply_rejected_across_users(self, db, SessionLocal):
        """User B cannot clear User A's lead's reply flag. The
        _check_ownership guard must fire before the update."""
        user_a = _make_user(db, "Nehal", "demo-member-008@company.example")
        user_b = _make_user(db, "Mark", "demo-member-007@company.example")
        lead = _make_lead(
            db, user_a,
            email="prospect@acme.com",
            draft_created_at=datetime(2026, 4, 8, 10, 0),
            replied_at=datetime(2026, 4, 8, 14, 0),
        )
        # Log in as user_b
        app = _build_app(SessionLocal, lambda: user_b)
        client = TestClient(app)
        resp = client.post(f"/api/conference/leads/{lead.id}/clear-reply")
        assert resp.status_code == 403
        db.refresh(lead)
        assert lead.replied_at is not None  # unchanged
