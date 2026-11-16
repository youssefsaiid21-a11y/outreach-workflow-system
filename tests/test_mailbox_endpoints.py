"""
End-to-end-ish tests for src/api/mailbox.py.

We stub providers via ``_resolve_provider`` so the router is the
actual code under test - including ``_annotate_campaigns``, the campaign
filter, the reply audit-row write, and the cross-user auth guards.

Only Outlook-based flows remain; Instantly has been removed from the project.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

_TEST_DB = Path(tempfile.gettempdir()) / f"outreach_test_mailbox_{os.getpid()}.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ.setdefault("MICROSOFT_CLIENT_ID", "test_client_id")

from src.db.database import SessionLocal, init_db  # noqa: E402
init_db()


def _purge():
    from src.db.models import (
        Campaign, EmailSequence, Prospect, Reply, SendLog, User,
    )
    db = SessionLocal()
    try:
        for m in (Reply, SendLog, EmailSequence, Prospect, Campaign, User):
            db.query(m).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def db_session():
    _purge()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _seed(db):
    from src.db.models import (
        Campaign, CampaignStatus, EmailSequence, Prospect, ProspectStatus,
        SequenceStatus, User, UserRole,
    )
    user_id = str(uuid.uuid4())
    db.add(User(
        id=user_id, name="Rep", email="rep@us.com", password_hash="x",
        role=UserRole.admin, created_at=datetime.now(timezone.utc),
    ))
    db.flush()
    cid = str(uuid.uuid4())
    db.add(Campaign(
        id=cid, name="Test Campaign", goal="meeting", value_prop="x",
        status=CampaignStatus.active, inbox_ids=["rep@us.com"],
        user_id=user_id, created_at=datetime.now(timezone.utc),
    ))
    db.flush()
    pid = str(uuid.uuid4())
    db.add(Prospect(
        id=pid, email="target@acme.com", first_name="Target", last_name="Prospect",
        company="Acme", title="VP",
        status=ProspectStatus.enrolled, user_id=user_id,
        created_at=datetime.now(timezone.utc),
    ))
    db.flush()
    sid = str(uuid.uuid4())
    db.add(EmailSequence(
        id=sid, campaign_id=cid, prospect_id=pid,
        approved=True,
        status=SequenceStatus.enrolled,
        email1_subject="Hi", email1_body="body",
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    return {
        "user_id": user_id,
        "campaign_id": cid,
        "prospect_id": pid,
    }


def _get_user(db, uid):
    from src.db.models import User
    return db.query(User).filter_by(id=uid).first()


# ── _annotate_campaigns (Outlook only) ──────────────────────────────────────


def test_annotate_campaigns_outlook_matches_by_sender_email(db_session):
    """Outlook inbox messages from a known prospect's email resolve to
    the owning campaign."""
    from src.api.mailbox import _annotate_campaigns
    seeded = _seed(db_session)

    msgs = [{
        "provider": "outlook",
        "folder": "inbox",
        "from_email": "target@acme.com",
        "to": [],
    }]
    out = _annotate_campaigns(db_session, msgs, [seeded["campaign_id"]])
    assert out[0]["campaign_id"] == seeded["campaign_id"]
    assert out[0]["prospect_id"] == seeded["prospect_id"]


def test_annotate_campaigns_outlook_sent_matches_by_recipient(db_session):
    """Outlook sent-folder messages to a known prospect's email resolve
    the same way."""
    from src.api.mailbox import _annotate_campaigns
    seeded = _seed(db_session)

    msgs = [{
        "provider": "outlook",
        "folder": "sent",
        "from_email": "rep@us.com",
        "to": [{"email": "target@acme.com", "name": "Target"}],
    }]
    out = _annotate_campaigns(db_session, msgs, [seeded["campaign_id"]])
    assert out[0]["campaign_id"] == seeded["campaign_id"]


def test_annotate_campaigns_outlook_unknown_sender_stays_null(db_session):
    """Outlook messages from senders not in the prospect table get
    campaign_id=None."""
    from src.api.mailbox import _annotate_campaigns
    seeded = _seed(db_session)

    msgs = [{
        "provider": "outlook",
        "folder": "inbox",
        "from_email": "random@stranger.com",
        "to": [],
    }]
    out = _annotate_campaigns(db_session, msgs, [seeded["campaign_id"]])
    assert out[0]["campaign_id"] is None


# ── Router endpoints with stubbed providers ─────────────────────────────────


class _StubProvider:
    """Shared stub returned by _resolve_provider."""

    def __init__(self, pages=None, detail=None, reply_result=None):
        self.pages = pages or {"items": [], "next_cursor": None}
        self.detail = detail or {"provider": "outlook", "id": "m1", "folder": "inbox"}
        self.reply_result = reply_result or {
            "sent": True,
            "provider_message_id": "new_msg",
            "subject": "Re: x",
            "reply_to": "target@acme.com",
        }
        self.mark_read_calls = 0

    kind = "outlook"

    def list_messages(self, **kw):
        return self.pages

    def get_message(self, **kw):
        return dict(self.detail)

    def send_reply(self, **kw):
        return dict(self.reply_result)

    def mark_read(self, **kw):
        self.mark_read_calls += 1

    def close(self):
        pass


def test_list_messages_returns_cursor_as_has_more(db_session, monkeypatch):
    from src.api import mailbox as mb
    seeded = _seed(db_session)
    user = _get_user(db_session, seeded["user_id"])

    stub = _StubProvider(pages={
        "items": [{"id": "m1", "provider": "outlook", "folder": "inbox",
                   "from_email": "x@y.com", "to": []}],
        "next_cursor": "next_abc",
    })
    monkeypatch.setattr(mb, "_resolve_provider", lambda inbox, cu: (stub, "rep@us.com"))

    res = mb.list_messages(
        inbox="outlook:rep@us.com", folder="inbox",
        q=None, campaign_id=None, unread=None, cursor=None, limit=50,
        db=db_session, current_user=user,
    )
    assert res["has_more"] is True
    assert res["next_cursor"] == "next_abc"


def test_reply_writes_audit_row(db_session, monkeypatch):
    """A successful reply via the mailbox API writes a Reply row with
    classification=outbound_reply so the inbox view reflects it."""
    from src.api import mailbox as mb
    from src.db.models import Reply
    seeded = _seed(db_session)
    user = _get_user(db_session, seeded["user_id"])

    stub = _StubProvider(
        detail={
            "provider": "outlook",
            "id": "m1",
            "folder": "inbox",
            "from_email": "target@acme.com",
            "to": [],
            "subject": "Intro",
        },
    )
    monkeypatch.setattr(mb, "_resolve_provider", lambda inbox, cu: (stub, "rep@us.com"))

    res = mb.reply_to_message(
        msg_id="m1",
        req=mb.ReplyRequest(body="Thanks, will review!"),
        inbox="outlook:rep@us.com",
        db=db_session, current_user=user,
    )
    assert res["sent"] is True
    assert res["reply_logged_id"] is not None

    rows = db_session.query(Reply).filter_by(classification="outbound_reply").all()
    assert len(rows) == 1
    assert rows[0].prospect_id == seeded["prospect_id"]
    assert rows[0].campaign_id == seeded["campaign_id"]
    assert rows[0].body == "Thanks, will review!"


def test_reply_rejects_empty_body(db_session, monkeypatch):
    from fastapi import HTTPException
    from src.api import mailbox as mb
    seeded = _seed(db_session)
    user = _get_user(db_session, seeded["user_id"])

    monkeypatch.setattr(mb, "_resolve_provider", lambda inbox, cu: (_StubProvider(), "rep@us.com"))
    with pytest.raises(HTTPException) as exc:
        mb.reply_to_message(
            msg_id="m1",
            req=mb.ReplyRequest(body="   "),
            inbox="outlook:rep@us.com",
            db=db_session, current_user=user,
        )
    assert exc.value.status_code == 422


# ── _resolve_provider validation ────────────────────────────────────────────


def test_resolve_provider_rejects_malformed_id():
    from fastapi import HTTPException
    from src.api.mailbox import _resolve_provider
    with pytest.raises(HTTPException) as exc:
        _resolve_provider("no-colon", None)
    assert exc.value.status_code == 400


def test_resolve_provider_rejects_unknown_kind():
    from fastapi import HTTPException
    from src.api.mailbox import _resolve_provider
    with pytest.raises(HTTPException) as exc:
        _resolve_provider("imap:x@y.com", None)
    assert exc.value.status_code == 400


def test_resolve_provider_rejects_cross_user_outlook(db_session, monkeypatch):
    from fastapi import HTTPException
    from src.api import mailbox as mb

    class _FakeUser:
        id = "userA"

    with pytest.raises(HTTPException) as exc:
        mb._resolve_provider("outlook:outlook_userB", _FakeUser())
    assert exc.value.status_code == 403
