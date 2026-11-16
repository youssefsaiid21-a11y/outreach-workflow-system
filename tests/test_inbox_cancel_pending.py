"""
Hardening - cancel-pending kill switch on the inbox endpoint.

Built in response to the Nasser v1.0 incident where removing a single
mistakenly-included lead required a manual operation. The new endpoint
makes that a first-class operator action.

Pins:

1. ``GET /inboxes/{email}/pending`` returns the list of pending
   send_logs for the current user, scoped to the inbox.
2. ``POST /inboxes/{email}/cancel-pending`` flips the local rows to
   ``cancelled``.
3. The local cancel ALWAYS lands.
4. The corresponding prospects are flipped to ``unsubscribed`` (kill
   switch semantics: "never contact this person again", not "skip this
   one campaign").
5. Cross-user isolation: rep A cannot cancel rep B's pending sends.

Note: Instantly has been removed. The cancel endpoint no longer calls
Instantly's delete_lead; tests that verified that API call are removed.
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

# Use a dedicated file-based SQLite so this module is COMPLETELY
# isolated from the global engine in src.db.database. Setting
# DATABASE_URL before any src.* import is critical - once src.db.database
# is imported, the global engine is bound and can't be changed.
import tempfile
_TEST_DB_PATH = Path(tempfile.gettempdir()) / f"outreach_test_cancel_{os.getpid()}.db"
if _TEST_DB_PATH.exists():
    _TEST_DB_PATH.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
from src.db.database import SessionLocal, init_db, engine  # noqa: E402
init_db()


def _purge_all():
    """Truncate every table this test module touches between tests so
    rows from one test don't leak into the next."""
    from src.db.models import (
        EmailSequence,
        Prospect,
        Campaign,
        SendLog,
        UnsubscribeEntry,
        User,
    )
    db = SessionLocal()
    try:
        for model in (SendLog, EmailSequence, Prospect, UnsubscribeEntry, Campaign, User):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def db_session():
    _purge_all()
    db = SessionLocal()
    try:
        yield db
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def _seed(db, *, inbox="sender@example.com", n_pending=2, instantly_cid=None, user_email="rep@example.com"):
    # instantly_cid kept as no-op for call-site compatibility
    from src.db.models import (
        Campaign,
        CampaignStatus,
        EmailSequence,
        Prospect,
        ProspectStatus,
        SendLog,
        SequenceStatus,
        User,
        UserRole,
    )

    user_id = str(uuid.uuid4())
    db.add(User(
        id=user_id, name="Rep", email=user_email,
        password_hash="x", role=UserRole.admin,
        created_at=datetime.now(timezone.utc),
    ))
    db.flush()

    cid = str(uuid.uuid4())
    db.add(Campaign(
        id=cid, name="Test Campaign",
        goal="meeting", value_prop="x",
        status=CampaignStatus.active,
        inbox_ids=[inbox],
        user_id=user_id,
        created_at=datetime.now(timezone.utc),
    ))
    db.flush()

    prospect_ids = []
    for i in range(n_pending):
        em = f"target{i}@example.com"
        pid = str(uuid.uuid4())
        db.add(Prospect(
            id=pid, email=em, first_name=f"P{i}", last_name="Last",
            company="Co", title="VP",
            status=ProspectStatus.enrolled,
            user_id=user_id,
            created_at=datetime.now(timezone.utc),
        ))
        db.flush()
        sid = str(uuid.uuid4())
        db.add(EmailSequence(
            id=sid, campaign_id=cid, prospect_id=pid,
            approved=True, status=SequenceStatus.enrolled,
            email1_subject="s", email1_body="b",
            created_at=datetime.now(timezone.utc),
        ))
        db.flush()
        db.add(SendLog(
            id=str(uuid.uuid4()),
            prospect_id=pid, campaign_id=cid, sequence_id=sid,
            step=1, subject="s", body_preview="b",
            status="pending",
            enrolled_at=datetime.now(timezone.utc),
            inbox_email=inbox,
        ))
        db.flush()
        prospect_ids.append((pid, em))
    db.commit()
    return {"user_id": user_id, "campaign_id": cid, "prospects": prospect_ids}


def test_list_pending_returns_only_current_user_rows(db_session):
    from src.api.inboxes import list_pending_for_inbox
    seeded = _seed(db_session)

    from src.db.models import User
    cu = db_session.query(User).filter_by(id=seeded["user_id"]).first()

    rows = list_pending_for_inbox(
        email="sender@example.com",
        campaign_id=None,
        db=db_session,
        current_user=cu,
    )
    assert len(rows) == 2
    assert {r["prospect_email"] for r in rows} == {"target0@example.com", "target1@example.com"}


def test_cancel_pending_marks_local_rows(db_session):
    from src.api.inboxes import cancel_pending_for_inbox
    from src.db.models import ProspectStatus, SendLog, Prospect, User

    seeded = _seed(db_session)
    cu = db_session.query(User).filter_by(id=seeded["user_id"]).first()

    result = cancel_pending_for_inbox(
        email="sender@example.com",
        campaign_id=None,
        db=db_session,
        current_user=cu,
    )

    assert result.cancelled_local == 2

    # Local rows flipped
    pending_left = db_session.query(SendLog).filter_by(status="pending").count()
    cancelled = db_session.query(SendLog).filter_by(status="cancelled").count()
    assert pending_left == 0
    assert cancelled == 2

    # Prospects unsubscribed
    unsubbed = db_session.query(Prospect).filter_by(status=ProspectStatus.unsubscribed).count()
    assert unsubbed == 2


def test_cancel_pending_respects_campaign_filter(db_session):
    """The optional campaign_id query param narrows the cancel to one
    campaign - operator can target a specific campaign without nuking
    the entire inbox queue."""
    from src.api.inboxes import cancel_pending_for_inbox
    from src.db.models import SendLog, User

    seeded = _seed(db_session)
    cu = db_session.query(User).filter_by(id=seeded["user_id"]).first()
    other_cid = str(uuid.uuid4())

    # Filter to a NON-existent campaign - should cancel zero.
    result = cancel_pending_for_inbox(
        email="sender@example.com",
        campaign_id=other_cid,
        db=db_session,
        current_user=cu,
    )

    assert result.cancelled_local == 0
    pending_left = db_session.query(SendLog).filter_by(status="pending").count()
    assert pending_left == 2  # nothing was touched


def test_cancel_pending_cross_user_isolation(db_session):
    """Rep A's cancel call must not touch rep B's pending sends."""
    from src.api.inboxes import cancel_pending_for_inbox
    from src.db.models import SendLog, User

    seeded_a = _seed(db_session, user_email="a@x.com")
    seeded_b = _seed(db_session, user_email="b@x.com")
    rep_a = db_session.query(User).filter_by(id=seeded_a["user_id"]).first()

    result = cancel_pending_for_inbox(
        email="sender@example.com",
        campaign_id=None,
        db=db_session,
        current_user=rep_a,
    )

    # Only rep A's 2 rows cancelled - rep B's 2 still pending.
    assert result.cancelled_local == 2
    pending_left = db_session.query(SendLog).filter_by(status="pending").count()
    assert pending_left == 2
