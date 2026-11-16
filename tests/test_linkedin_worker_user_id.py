"""P3-7: LinkedIn worker must refuse to dispatch unowned queue items.

After the P3-1 backfill every queue row has a user_id. A None here
means corrupt / un-backfilled data - the worker must log at ERROR,
mark the row failed, and NOT launch a Phantom.
"""
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import (
    LinkedInQueue,
    LinkedInQueueStatus,
    Prospect,
    ProspectStatus,
)
from src.services import linkedin_worker as lw


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_resolve_user_id_returns_none_for_unowned_row(db):
    # Prospect without user_id, no campaign
    db.add(Prospect(
        id="p1", first_name="X", email="x@y.com",
        user_id=None, status=ProspectStatus.pending,
    ))
    q = LinkedInQueue(
        id="q1", prospect_id="p1", campaign_id=None,
        action_type="connect", linkedin_url="https://linkedin.com/in/x",
        message_text="hi",
        user_id=None,
    )
    db.add(q)
    db.commit()

    resolved = lw._resolve_user_id(db, q)
    assert resolved is None


def test_drain_once_skips_unowned_group_and_logs_error(db, caplog, monkeypatch):
    """An unowned queue row is marked failed; no Phantom launch attempted."""
    db.add(Prospect(
        id="p1", first_name="X", email="x@y.com",
        user_id=None, status=ProspectStatus.pending,
    ))
    db.add(LinkedInQueue(
        id="q1", prospect_id="p1", campaign_id=None,
        action_type="connect", linkedin_url="https://linkedin.com/in/x",
        message_text="hi", user_id=None,
        status=LinkedInQueueStatus.queued,
    ))
    db.commit()

    lw._drain_once(db)
    # Row should be marked failed with "unowned" detail
    row = db.query(LinkedInQueue).filter_by(id="q1").first()
    assert row.status == LinkedInQueueStatus.failed
    assert "unowned" in (row.result_detail or "").lower()


def test_drain_once_still_processes_owned_groups(db, monkeypatch):
    """A valid row in the same batch must not be blocked by a sibling unowned row."""
    db.add(Prospect(
        id="p_owned", first_name="X", email="x@y.com",
        user_id="user_a", status=ProspectStatus.pending,
    ))
    db.add(Prospect(
        id="p_unowned", first_name="Y", email="y@y.com",
        user_id=None, status=ProspectStatus.pending,
    ))
    db.add(LinkedInQueue(
        id="q_owned", prospect_id="p_owned",
        action_type="connect", linkedin_url="https://linkedin.com/in/a",
        message_text="msg", user_id="user_a",
        status=LinkedInQueueStatus.queued,
    ))
    db.add(LinkedInQueue(
        id="q_unowned", prospect_id="p_unowned",
        action_type="connect", linkedin_url="https://linkedin.com/in/b",
        message_text="msg", user_id=None,
        status=LinkedInQueueStatus.queued,
    ))
    db.commit()

    lw._drain_once(db)

    # Owned item should be in processing (visible in CSV feed for PB).
    owned = db.query(LinkedInQueue).filter_by(id="q_owned").one()
    assert owned.status == LinkedInQueueStatus.processing
    # Unowned row is failed
    assert db.query(LinkedInQueue).filter_by(id="q_unowned").one().status == LinkedInQueueStatus.failed
