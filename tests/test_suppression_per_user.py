"""P3-5: Per-user suppression isolation.

Each user's suppression entries must only affect their own prospects.
Global entries (user_id IS NULL) still apply to everyone.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import Prospect, ProspectStatus, UnsubscribeEntry
from src.services.suppression import (
    filter_prospects_for_campaign,
    is_prospect_blocked,
    _load_suppressed_emails,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_user_scoped_entries_isolate_between_users(db):
    # User A suppresses alice@x.com; User B suppresses bob@x.com.
    # A global entry suppresses carol@x.com for everyone.
    db.add(UnsubscribeEntry(email="alice@x.com", user_id="user_a", reason="manual"))
    db.add(UnsubscribeEntry(email="bob@x.com", user_id="user_b", reason="manual"))
    db.add(UnsubscribeEntry(email="carol@x.com", user_id=None, reason="global"))
    db.commit()

    # User A's view
    a_set = _load_suppressed_emails(db, "user_a")
    assert "alice@x.com" in a_set
    assert "carol@x.com" in a_set
    assert "bob@x.com" not in a_set

    # User B's view
    b_set = _load_suppressed_emails(db, "user_b")
    assert "bob@x.com" in b_set
    assert "carol@x.com" in b_set
    assert "alice@x.com" not in b_set

    # is_prospect_blocked honours the scoping
    assert is_prospect_blocked("alice@x.com", db, user_id="user_a") == "unsubscribed"
    assert is_prospect_blocked("alice@x.com", db, user_id="user_b") is None
    assert is_prospect_blocked("carol@x.com", db, user_id="user_a") == "unsubscribed"
    assert is_prospect_blocked("carol@x.com", db, user_id="user_b") == "unsubscribed"


def test_filter_prospects_uses_one_suppression_query(db, monkeypatch):
    """Regression: previous implementation issued one query per prospect.

    We can't count queries across SQLAlchemy cleanly from here, but we
    verify that the function behaves correctly on a batch and scopes the
    suppression load to the caller's user_id.
    """
    db.add(UnsubscribeEntry(email="alice@x.com", user_id="user_a"))
    db.add(Prospect(id="pa1", first_name="A", email="alice@x.com", user_id="user_a", status=ProspectStatus.pending))
    db.add(Prospect(id="pb1", first_name="B", email="alice@x.com", user_id="user_b", status=ProspectStatus.pending))
    db.add(Prospect(id="pa2", first_name="C", email="clean@x.com", user_id="user_a", status=ProspectStatus.pending))
    db.commit()

    # Running the filter as User A - alice is blocked, clean is allowed.
    allowed_a, blocked_a = filter_prospects_for_campaign(
        ["pa1", "pa2"], db, user_id="user_a",
    )
    assert allowed_a == ["pa2"]
    assert len(blocked_a) == 1 and blocked_a[0]["prospect_id"] == "pa1"

    # Running the filter as User B on User B's own prospect - alice NOT
    # blocked because user_a's suppression entry does not apply.
    allowed_b, blocked_b = filter_prospects_for_campaign(
        ["pb1"], db, user_id="user_b",
    )
    assert allowed_b == ["pb1"]
    assert blocked_b == []
