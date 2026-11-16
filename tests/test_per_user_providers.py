"""
P3-2 / deep audit CRITICAL-3 + HIGH-7 - per-user provider registry.

Verifies:

1. `_get_providers(user_id)` returns distinct cached instances per user.
2. Two users with different `__user__{uid}__INSTANTLY_API_KEY` / campaign
   settings get provider pairs wired to their own credentials - no env-var
   bleed from the process.
3. `_reset_providers(user_id)` evicts exactly one entry; `_reset_providers()`
   clears everything.
4. The legacy ``None`` cache slot still falls back to env vars so
   unauthenticated code paths (tests, first-run installs, CLI) keep
   working.

Additionally verifies P3-4: `LeadSequenceStateRepository.get_due(user_id=X)`
filters rows by user_id.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import AppSetting, Prospect
from src.orchestration import orm as _orch_orm  # noqa: F401
from src.orchestration.api import (
    _get_providers,
    _providers_cache,
    _reset_providers,
)
from src.orchestration.enums import OrchestrationStatus
from src.orchestration.models import LeadSequenceState
from src.orchestration.providers import (
    PhantomBusterLinkedInProvider,
    StubLinkedInProvider,
)
from src.orchestration.repositories import LeadSequenceStateRepository


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _clear_cache():
    """Every test starts with an empty provider cache so tests are independent."""
    _reset_providers()
    yield
    _reset_providers()


def _set_user_setting(db, user_id: str, key: str, value: str) -> None:
    db.add(AppSetting(key=f"__user__{user_id}__{key}", value=value))
    db.commit()


# ── P3-2: per-user provider isolation ────────────────────────────────────


def test_providers_cached_per_user(session, monkeypatch):
    """Two users get distinct provider instances; re-requesting the same
    user returns the same cached provider."""
    # No env vars - explicitly clear so we know the settings take over.
    monkeypatch.delenv("PHANTOMBUSTER_API_KEY", raising=False)
    monkeypatch.delenv("PHANTOMBUSTER_CONNECT_AGENT_ID", raising=False)
    monkeypatch.delenv("PHANTOMBUSTER_MESSAGE_AGENT_ID", raising=False)

    _set_user_setting(session, "user-A", "PHANTOMBUSTER_API_KEY", "pb-A")
    _set_user_setting(session, "user-A", "PHANTOMBUSTER_CONNECT_AGENT_ID", "connect-A")
    _set_user_setting(session, "user-A", "PHANTOMBUSTER_MESSAGE_AGENT_ID", "message-A")

    _set_user_setting(session, "user-B", "PHANTOMBUSTER_API_KEY", "pb-B")
    _set_user_setting(session, "user-B", "PHANTOMBUSTER_CONNECT_AGENT_ID", "connect-B")
    _set_user_setting(session, "user-B", "PHANTOMBUSTER_MESSAGE_AGENT_ID", "message-B")

    linkedin_a = _get_providers(user_id="user-A", db=session)
    linkedin_b = _get_providers(user_id="user-B", db=session)

    # Distinct instances
    assert linkedin_a is not linkedin_b

    # Credentials isolated - check via private attributes set in constructors
    assert isinstance(linkedin_a, PhantomBusterLinkedInProvider)
    assert isinstance(linkedin_b, PhantomBusterLinkedInProvider)
    assert linkedin_a._api_key == "pb-A"
    assert linkedin_b._api_key == "pb-B"
    assert linkedin_a._connect_agent_id == "connect-A"
    assert linkedin_b._connect_agent_id == "connect-B"
    assert linkedin_a._message_agent_id == "message-A"
    assert linkedin_b._message_agent_id == "message-B"

    # Cache hit: second call returns same object
    linkedin_a2 = _get_providers(user_id="user-A", db=session)
    assert linkedin_a2 is linkedin_a


def test_env_fallback_when_no_user_setting(monkeypatch, session):
    """With no per-user row, the provider falls back to env vars."""
    monkeypatch.delenv("PHANTOMBUSTER_API_KEY", raising=False)

    linkedin = _get_providers(user_id="user-X", db=session)
    # No PB key → stub LinkedIn
    assert isinstance(linkedin, StubLinkedInProvider)


def test_legacy_none_user_slot_reads_env(monkeypatch, session):
    """Calling with no user_id should still work (tests, CLI, first-run)."""
    monkeypatch.delenv("PHANTOMBUSTER_API_KEY", raising=False)

    linkedin = _get_providers(user_id=None, db=session)
    assert isinstance(linkedin, StubLinkedInProvider)
    assert None in _providers_cache


def test_reset_providers_per_user(session, monkeypatch):
    """Evicting one user's entry leaves others intact."""
    monkeypatch.delenv("PHANTOMBUSTER_API_KEY", raising=False)
    _set_user_setting(session, "user-A", "PHANTOMBUSTER_API_KEY", "pb-A")
    _set_user_setting(session, "user-B", "PHANTOMBUSTER_API_KEY", "pb-B")

    _get_providers(user_id="user-A", db=session)
    _get_providers(user_id="user-B", db=session)
    assert "user-A" in _providers_cache
    assert "user-B" in _providers_cache

    _reset_providers(user_id="user-A")
    assert "user-A" not in _providers_cache
    assert "user-B" in _providers_cache

    _reset_providers()
    assert _providers_cache == {}


def test_per_user_key_rotation(session, monkeypatch):
    """After updating a user's app_settings and evicting the cache, the
    next lookup picks up the new key."""
    monkeypatch.delenv("PHANTOMBUSTER_API_KEY", raising=False)
    _set_user_setting(session, "u1", "PHANTOMBUSTER_API_KEY", "old-key")

    linkedin1 = _get_providers(user_id="u1", db=session)
    assert isinstance(linkedin1, PhantomBusterLinkedInProvider)
    assert linkedin1._api_key == "old-key"

    # Rotate
    row = session.query(AppSetting).filter_by(key="__user__u1__PHANTOMBUSTER_API_KEY").first()
    row.value = "new-key"
    session.commit()

    # Without eviction, cache still has old key
    linkedin_cached = _get_providers(user_id="u1", db=session)
    assert linkedin_cached._api_key == "old-key"

    _reset_providers(user_id="u1")
    linkedin2 = _get_providers(user_id="u1", db=session)
    assert linkedin2._api_key == "new-key"
    assert linkedin2 is not linkedin1


# ── P3-4: per-user scheduler filter ──────────────────────────────────────


def _make_state(state_id: str, lead_id: str, user_id: str | None, now: datetime) -> LeadSequenceState:
    return LeadSequenceState(
        state_id=state_id,
        lead_id=lead_id,
        sequence_id="seq",
        status=OrchestrationStatus.active,
        current_step_index=0,
        enrolled_at=now - timedelta(days=1),
        scheduled_next_at=now - timedelta(hours=1),
    )


def test_get_due_filters_by_user_id(session):
    """P3-4: scheduler filter keyed on the new user_id column."""
    now = datetime.now(timezone.utc)
    session.add(Prospect(id="lead-A", email="a@a.com", first_name="A", last_name="A", user_id="user-A"))
    session.add(Prospect(id="lead-B", email="b@b.com", first_name="B", last_name="B", user_id="user-B"))
    session.commit()

    repo = LeadSequenceStateRepository(session)
    repo.save(_make_state("sA", "lead-A", "user-A", now))
    repo.save(_make_state("sB", "lead-B", "user-B", now))
    session.commit()

    # Back-fill user_id directly since the ORM save() may not copy it yet
    # (LeadSequenceState Pydantic model doesn't carry user_id in Phase 3
    # - the column is populated at enrol time by the orchestration API).
    from src.orchestration.orm import OrchSequenceStateORM
    session.query(OrchSequenceStateORM).filter_by(state_id="sA").update({"user_id": "user-A"})
    session.query(OrchSequenceStateORM).filter_by(state_id="sB").update({"user_id": "user-B"})
    session.commit()

    # No filter → both
    all_rows = repo.get_due(now)
    assert {r.state_id for r in all_rows} == {"sA", "sB"}

    # Filtered to user-A
    a_only = repo.get_due(now, user_id="user-A")
    assert {r.state_id for r in a_only} == {"sA"}

    # Filtered to user-B
    b_only = repo.get_due(now, user_id="user-B")
    assert {r.state_id for r in b_only} == {"sB"}

    # Filtered to non-existent user
    none_rows = repo.get_due(now, user_id="user-C")
    assert none_rows == []
