"""
P2-6 / deep audit MEDIUM-3 + MEDIUM-10 - atomic scheduler bookkeeping.

1. `LeadSequenceStateRepository.get_due()` should add `FOR UPDATE
   SKIP LOCKED` only on Postgres. SQLite (the test backend) returns
   the same rows without the clause - verify the call still works
   and the result is correct.
2. `_upsert_scheduler_last_run()` writes the latest timestamp via a
   dialect-aware atomic upsert. Repeated calls leave exactly one
   row with the most recent value (no duplicates, no torn writes).
"""
from __future__ import annotations

import os
import sys
import time
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
from src.orchestration.enums import OrchestrationStatus
from src.orchestration.models import LeadSequenceState
from src.orchestration.repositories import LeadSequenceStateRepository
from src.orchestration.scheduler import _upsert_scheduler_last_run


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


def test_get_due_returns_due_states(session):
    """SQLite path: get_due works and returns the due rows in order."""
    repo = LeadSequenceStateRepository(session)
    now = datetime.now(timezone.utc)

    # Two due states (one older), one not yet due
    s1 = LeadSequenceState(
        state_id="s1",
        lead_id="lead-1",
        sequence_id="seq",
        status=OrchestrationStatus.active,
        current_step_index=0,
        enrolled_at=now - timedelta(days=2),
        scheduled_next_at=now - timedelta(hours=2),
    )
    s2 = LeadSequenceState(
        state_id="s2",
        lead_id="lead-1",
        sequence_id="seq",
        status=OrchestrationStatus.active,
        current_step_index=0,
        enrolled_at=now - timedelta(days=1),
        scheduled_next_at=now - timedelta(hours=1),
    )
    s3 = LeadSequenceState(
        state_id="s3",
        lead_id="lead-1",
        sequence_id="seq",
        status=OrchestrationStatus.active,
        current_step_index=0,
        enrolled_at=now,
        scheduled_next_at=now + timedelta(hours=2),
    )
    repo.upsert_from_pydantic(s1)
    repo.upsert_from_pydantic(s2)
    repo.upsert_from_pydantic(s3)
    session.commit()

    due = repo.get_due(now)
    due_ids = [r.state_id for r in due]
    assert due_ids == ["s1", "s2"]


def test_get_due_dialect_branch_no_crash_on_sqlite(session):
    """The Postgres-only with_for_update branch must NOT fire on SQLite."""
    repo = LeadSequenceStateRepository(session)
    # Smoke test - call must succeed without errors.
    rows = repo.get_due(datetime.now(timezone.utc))
    assert isinstance(rows, list)


def test_upsert_scheduler_last_run_creates_row(session):
    assert session.query(AppSetting).filter_by(key="scheduler_last_run").first() is None
    _upsert_scheduler_last_run(session)
    row = session.query(AppSetting).filter_by(key="scheduler_last_run").first()
    assert row is not None
    assert row.value  # ISO timestamp string


def test_upsert_scheduler_last_run_idempotent_overwrite(session):
    """Repeated calls leave exactly one row with the most recent value."""
    _upsert_scheduler_last_run(session)
    first = session.query(AppSetting).filter_by(key="scheduler_last_run").first().value

    # Tiny sleep so the second timestamp differs
    time.sleep(0.01)

    _upsert_scheduler_last_run(session)
    second = session.query(AppSetting).filter_by(key="scheduler_last_run").first().value

    assert second != first
    assert second > first  # ISO sort is lexicographically chronological
    # Still exactly one row.
    assert session.query(AppSetting).filter_by(key="scheduler_last_run").count() == 1


def test_upsert_many_writes_no_duplicates(session):
    """Hammer the upsert to verify single-row invariant."""
    for _ in range(50):
        _upsert_scheduler_last_run(session)
    assert session.query(AppSetting).filter_by(key="scheduler_last_run").count() == 1
