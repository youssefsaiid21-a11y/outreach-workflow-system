"""
P2-5 / deep audit HIGH-9 - structured PhantomBuster success detection.

The old success detection was substring matching against a hardcoded set
of strings (and a console-output regex). A single PhantomBuster UI wording
change would silently break it, and missing data fell through to "mark
done optimistically".

These tests pin down the new behaviour:

1. Schema-driven classifier returns the right verdict for each variant.
2. Unknown action_types and missing fields return "unknown" - never
   "success".
3. `_process_results` leaves a queue row in `processing` when the row
   verdict is unknown (no optimistic done).
4. `_process_results` refuses to operate on action_types with no schema.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import LinkedInQueue, LinkedInQueueStatus, Prospect
from src.services.linkedin_batch import (
    _process_results,
    classify_phantom_row,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    db.add(Prospect(
        id="lead-1",
        email="x@x.com",
        first_name="X",
        last_name="Y",
        company="Acme",
        title="HSE",
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


# ── classify_phantom_row ───────────────────────────────────────────────────


def test_classify_success_via_boolean_flag():
    row = {"profileUrl": "https://x", "connectionRequestSent": True}
    verdict, _ = classify_phantom_row(row, "connect")
    assert verdict == "success"


def test_classify_success_via_status_string():
    row = {"profileUrl": "https://x", "status": "Sent"}
    verdict, _ = classify_phantom_row(row, "connect")
    assert verdict == "success"


def test_classify_neutral_already_connected():
    row = {"profileUrl": "https://x", "alreadyConnected": True}
    verdict, _ = classify_phantom_row(row, "connect")
    assert verdict == "neutral"


def test_classify_neutral_via_status():
    row = {"profileUrl": "https://x", "status": "already connected"}
    verdict, _ = classify_phantom_row(row, "connect")
    assert verdict == "neutral"


def test_classify_failed_via_error_field():
    row = {"profileUrl": "https://x", "error": "profile not found"}
    verdict, detail = classify_phantom_row(row, "connect")
    assert verdict == "failed"
    assert "profile not found" in detail


def test_classify_unknown_when_no_fields():
    row = {"profileUrl": "https://x"}
    verdict, _ = classify_phantom_row(row, "connect")
    assert verdict == "unknown"


def test_classify_unknown_for_unsupported_action_type():
    row = {"profileUrl": "https://x", "status": "sent"}
    verdict, _ = classify_phantom_row(row, "scrape_profile")
    assert verdict == "unknown"


def test_classify_message_success():
    row = {"profileUrl": "https://x", "messageSent": True}
    verdict, _ = classify_phantom_row(row, "message")
    assert verdict == "success"


def test_classify_failed_status_string():
    row = {"profileUrl": "https://x", "status": "error: rate-limited"}
    verdict, _ = classify_phantom_row(row, "connect")
    assert verdict == "failed"


# ── _process_results ───────────────────────────────────────────────────────


def _seed_queue(session, url: str, action_type: str = "connect") -> LinkedInQueue:
    item = LinkedInQueue(
        id=f"q-{url[-3:]}",
        prospect_id="lead-1",
        campaign_id=None,
        action_type=action_type,
        linkedin_url=url,
        message_text=None,
        status=LinkedInQueueStatus.processing,
    )
    session.add(item)
    session.commit()
    return item


def _make_client(result_object):
    client = MagicMock()
    client.get_container_result.return_value = {"resultObject": result_object}
    return client


def test_process_results_marks_success(session):
    url = "https://linkedin.com/in/alpha"
    item = _seed_queue(session, url)
    client = _make_client([
        {"profileUrl": url, "status": "sent"},
    ])
    url_to_items = {url.rstrip("/").lower(): [item]}

    _process_results(client, "container-1", "agent-1", url_to_items, "connect", session)

    session.refresh(item)
    assert item.status == LinkedInQueueStatus.done


def test_process_results_unknown_keeps_processing(session):
    """Critical regression guard: missing schema fields must NOT auto-done."""
    url = "https://linkedin.com/in/beta"
    item = _seed_queue(session, url)
    # Row is present but has no recognised schema fields
    client = _make_client([
        {"profileUrl": url, "totallyMadeUpField": True},
    ])
    url_to_items = {url.rstrip("/").lower(): [item]}

    _process_results(client, "container-2", "agent-1", url_to_items, "connect", session)

    session.refresh(item)
    assert item.status == LinkedInQueueStatus.processing, \
        "unknown verdict must NOT mark the queue row done"


def test_process_results_no_row_keeps_processing(session):
    """If the Phantom returned nothing for this URL, do not optimistically done."""
    url = "https://linkedin.com/in/gamma"
    item = _seed_queue(session, url)
    client = _make_client([])  # empty results
    url_to_items = {url.rstrip("/").lower(): [item]}

    _process_results(client, "container-3", "agent-1", url_to_items, "connect", session)

    session.refresh(item)
    assert item.status == LinkedInQueueStatus.processing


def test_process_results_failed_marks_failed(session):
    url = "https://linkedin.com/in/delta"
    item = _seed_queue(session, url)
    client = _make_client([
        {"profileUrl": url, "error": "profile is private"},
    ])
    url_to_items = {url.rstrip("/").lower(): [item]}

    _process_results(client, "container-4", "agent-1", url_to_items, "connect", session)

    session.refresh(item)
    assert item.status == LinkedInQueueStatus.failed
    assert "private" in (item.result_detail or "")


def test_process_results_refuses_unsupported_action_type(session):
    url = "https://linkedin.com/in/epsilon"
    item = _seed_queue(session, url, action_type="scrape")
    client = _make_client([{"profileUrl": url, "status": "sent"}])
    url_to_items = {url.rstrip("/").lower(): [item]}

    _process_results(client, "container-5", "agent-1", url_to_items, "scrape", session)

    session.refresh(item)
    # Refused - queue row untouched.
    assert item.status == LinkedInQueueStatus.processing
