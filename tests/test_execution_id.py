"""
P2-7 / deep audit HIGH-6 - execution_id must come from a single helper.

Verifies:
1. `build_execution_id()` produces a deterministic format and rejects
   bad attempt numbers.
2. After a failed execution, `count_attempts()` includes that failed row
   so the next attempt strictly increments.
3. `exists()` now treats a failed row as "exists" - retries must use a
   fresh execution_id from a higher attempt number.
4. Scheduler and dispatcher agree on the format (smoke test).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.orchestration import orm as _orch_orm  # noqa: F401
from src.orchestration.enums import StepResultStatus, StepType
from src.orchestration.models import ActionExecution
from src.orchestration.repositories import (
    ActionExecutionRepository,
    build_execution_id,
)


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


def _make_exec(execution_id: str, status: StepResultStatus) -> ActionExecution:
    return ActionExecution(
        execution_id=execution_id,
        lead_id="lead-1",
        step_id="email1",
        action_type=StepType.send_email,
        status=status,
        attempted_at=datetime.now(timezone.utc),
        result_payload=None,
        error_message=None,
        state_id="state-1",
    )


def test_build_execution_id_format():
    assert build_execution_id("s1", "step1", 1) == "s1_step1_1"
    assert build_execution_id("s1", "step1", 7) == "s1_step1_7"


def test_build_execution_id_rejects_bad_attempt():
    with pytest.raises(ValueError):
        build_execution_id("s1", "step1", 0)
    with pytest.raises(ValueError):
        build_execution_id("s1", "step1", -1)


def test_count_attempts_includes_failed(session):
    """P2-7: count_attempts() now counts failed rows so retries increment."""
    repo = ActionExecutionRepository(session)
    repo.save(_make_exec("state-1_email1_1", StepResultStatus.failed))
    session.flush()

    assert repo.count_attempts("state-1", "email1") == 1
    next_attempt = repo.count_attempts("state-1", "email1") + 1
    assert next_attempt == 2

    # Retry produces a fresh, non-colliding execution_id
    next_id = build_execution_id("state-1", "email1", next_attempt)
    assert next_id == "state-1_email1_2"


def test_exists_includes_failed(session):
    """P2-7: failed rows count as 'exists' - retries must use a new id."""
    repo = ActionExecutionRepository(session)
    repo.save(_make_exec("state-1_email1_1", StepResultStatus.failed))
    session.flush()

    # The OLD failed id is now treated as existing → idempotency guard fires
    assert repo.exists("state-1_email1_1") is True
    # A fresh attempt id is unique and not blocked
    assert repo.exists("state-1_email1_2") is False


def test_executed_row_blocks_same_id_retry(session):
    repo = ActionExecutionRepository(session)
    repo.save(_make_exec("state-1_email1_1", StepResultStatus.executed))
    session.flush()

    assert repo.exists("state-1_email1_1") is True
    assert repo.count_attempts("state-1", "email1") == 1


def test_dispatcher_and_scheduler_agree_on_format():
    """Smoke test: both call sites import the same helper."""
    from src.orchestration.dispatcher import build_execution_id as d_build
    from src.orchestration.scheduler import build_execution_id as s_build

    assert d_build is s_build is build_execution_id
    assert d_build("a", "b", 3) == s_build("a", "b", 3) == "a_b_3"
