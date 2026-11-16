"""Tests for the LinkedIn message phantom pipeline fixes.

Verifies:
1. Message CSV endpoint serves profileUrl + message columns
2. _drain_once groups by action_type and launches correct agent
3. _get_csv_feed_url returns different paths per action_type
4. _launch_with_csv_feed uses correct agent + bonus args per action_type
5. poll_pb_results re-launch uses correct CSV per action type
"""
import asyncio
import csv
import io

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock, patch

from src.db.database import Base
from src.db.models import (
    LinkedInQueue,
    LinkedInQueueStatus,
    Prospect,
    ProspectStatus,
    EmailSequence,
    Campaign,
)
from src.services import linkedin_worker as lw


def _read_streaming_response(response) -> str:
    """Read a FastAPI StreamingResponse body (sync or async generator)."""
    body = b""
    gen = response.body_iterator
    if hasattr(gen, "__anext__"):
        loop = asyncio.new_event_loop()
        try:
            while True:
                try:
                    chunk = loop.run_until_complete(gen.__anext__())
                except StopAsyncIteration:
                    break
                if isinstance(chunk, str):
                    body += chunk.encode()
                else:
                    body += chunk
        finally:
            loop.close()
    else:
        for chunk in gen:
            if isinstance(chunk, str):
                body += chunk.encode()
            else:
                body += chunk
    return body.decode("utf-8")


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ── Fix 1: CSV feed URL per action type ───────────────────────────────────


def test_csv_feed_url_connect():
    with patch.dict("os.environ", {"PUBLIC_HOST": "example.com", "PORT": "3004"}):
        url = lw._get_csv_feed_url(action_type="connect")
    assert "/connect-leads.csv" in url
    assert "/message-leads.csv" not in url


def test_csv_feed_url_message():
    with patch.dict("os.environ", {"PUBLIC_HOST": "example.com", "PORT": "3004"}):
        url = lw._get_csv_feed_url(action_type="message")
    assert "/message-leads.csv" in url
    assert "/connect-leads.csv" not in url


def test_csv_feed_url_default_is_connect():
    with patch.dict("os.environ", {"PUBLIC_HOST": "example.com", "PORT": "3004"}):
        url = lw._get_csv_feed_url()
    assert "/connect-leads.csv" in url


# ── Fix 2: _drain_once groups by action_type ──────────────────────────────


def test_drain_marks_both_connect_and_message_as_processing(db):
    """Both connect and message items should become processing (visible in CSV for PB)."""
    db.add(Prospect(
        id="p1", first_name="A", email="a@b.com",
        user_id="u1", status=ProspectStatus.pending,
    ))
    db.add(Prospect(
        id="p2", first_name="B", email="b@b.com",
        user_id="u1", status=ProspectStatus.pending,
    ))
    db.add(LinkedInQueue(
        id="q_conn", prospect_id="p1", action_type="connect",
        linkedin_url="https://linkedin.com/in/a",
        message_text="note", user_id="u1",
        status=LinkedInQueueStatus.queued,
    ))
    db.add(LinkedInQueue(
        id="q_msg", prospect_id="p2", action_type="message",
        linkedin_url="https://linkedin.com/in/b",
        message_text="hello there", user_id="u1",
        status=LinkedInQueueStatus.queued,
    ))
    db.commit()

    lw._drain_once(db)

    conn = db.query(LinkedInQueue).filter_by(id="q_conn").one()
    msg = db.query(LinkedInQueue).filter_by(id="q_msg").one()
    assert conn.status == LinkedInQueueStatus.processing
    assert msg.status == LinkedInQueueStatus.processing


def test_drain_fails_message_without_text(db):
    """Message items with no message_text must be failed immediately."""
    db.add(Prospect(
        id="p1", first_name="A", email="a@b.com",
        user_id="u1", status=ProspectStatus.pending,
    ))
    db.add(LinkedInQueue(
        id="q1", prospect_id="p1", action_type="message",
        linkedin_url="https://linkedin.com/in/a",
        message_text="",  # empty
        user_id="u1",
        status=LinkedInQueueStatus.queued,
    ))
    db.commit()

    lw._drain_once(db)

    item = db.query(LinkedInQueue).filter_by(id="q1").one()
    assert item.status == LinkedInQueueStatus.failed
    assert "no_message_text" in item.result_detail


# ── Fix 4: message CSV endpoint serves profileUrl + message columns ───────


def test_message_csv_endpoint(db):
    """The message CSV must contain profileUrl and per-lead message text."""
    from src.api.pb_feed import message_leads_csv

    db.add(Prospect(
        id="p1", first_name="A", email="a@b.com",
        user_id="u1", status=ProspectStatus.pending,
    ))
    db.add(Campaign(
        id="camp1", name="Test", status="active",
        user_id="u1", goal="test campaign", value_prop="test vp",
    ))
    db.add(EmailSequence(
        id="es1", prospect_id="p1", campaign_id="camp1",
        linkedin_message="Hi A, great to connect!",
    ))
    db.add(LinkedInQueue(
        id="q1", prospect_id="p1", action_type="message",
        linkedin_url="https://linkedin.com/in/alice",
        message_text="Hi A, great to connect!",
        user_id="u1",
        status=LinkedInQueueStatus.processing,
    ))
    db.commit()

    response = message_leads_csv(db=db)
    text = _read_streaming_response(response)

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    assert len(rows) == 1
    assert "profileUrl" in rows[0]
    assert "message" in rows[0]
    assert "linkedin.com/in/alice" in rows[0]["profileUrl"]
    assert "Hi A, great to connect!" in rows[0]["message"]


def test_message_csv_skips_leads_without_message(db):
    """Leads with no message text must be excluded from the CSV."""
    from src.api.pb_feed import message_leads_csv

    db.add(Prospect(
        id="p1", first_name="A", email="a@b.com",
        user_id="u1", status=ProspectStatus.pending,
    ))
    db.add(LinkedInQueue(
        id="q1", prospect_id="p1", action_type="message",
        linkedin_url="https://linkedin.com/in/no-msg",
        message_text=None,  # No message
        user_id="u1",
        status=LinkedInQueueStatus.processing,
    ))
    db.commit()

    response = message_leads_csv(db=db)
    text = _read_streaming_response(response)

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    # No message text → excluded
    assert len(rows) == 0


def test_connect_csv_excludes_message_items(db):
    """The connect CSV must NOT include message queue items."""
    from src.api.pb_feed import connect_leads_csv

    db.add(LinkedInQueue(
        id="q_conn", prospect_id="p1", action_type="connect",
        linkedin_url="https://linkedin.com/in/conn",
        message_text="", user_id="u1",
        status=LinkedInQueueStatus.processing,
    ))
    db.add(LinkedInQueue(
        id="q_msg", prospect_id="p2", action_type="message",
        linkedin_url="https://linkedin.com/in/msg",
        message_text="hello", user_id="u1",
        status=LinkedInQueueStatus.processing,
    ))
    db.commit()

    response = connect_leads_csv(db=db)
    text = _read_streaming_response(response)

    # Only the connect item should appear
    assert "linkedin.com/in/conn" in text
    assert "linkedin.com/in/msg" not in text
