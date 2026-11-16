"""
P2-4 / deep audit CRITICAL-2 - LinkedIn handlers share the scheduler session.

The old handlers opened a fresh `SessionLocal()` inside a transaction the
scheduler already owned and committed the LinkedIn queue row independently.
If the scheduler's later commit rolled back, the queue row had already been
committed → split-brain.

These tests verify:
1. The queue helper `queue_linkedin_action_no_commit()` only flushes, never
   commits, so a session rollback cleans up the row.
2. The connect and message handlers refuse to run if the dispatcher did not
   inject `self._db` (fail-closed).
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

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import LinkedInQueue, LinkedInQueueStatus, Prospect


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    # Ensure orchestration orm is imported too
    from src.orchestration import orm as _orm  # noqa: F401
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    # Seed one prospect so FK constraints hold
    db.add(Prospect(
        id="lead-1",
        email="x@x.com",
        first_name="X",
        last_name="Y",
        company="Acme",
        title="HSE Manager",
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def test_queue_no_commit_rolls_back_with_session(session):
    """The no-commit helper must leave the row vulnerable to session rollback."""
    from src.services.linkedin_batch import queue_linkedin_action_no_commit

    queue_id = queue_linkedin_action_no_commit(
        db=session,
        prospect_id="lead-1",
        campaign_id=None,
        action_type="connect",
        linkedin_url="https://linkedin.com/in/x",
        message_text="Hi",
    )
    # Row is visible inside the transaction...
    assert session.query(LinkedInQueue).filter_by(id=queue_id).first() is not None

    session.rollback()

    # ...and gone after rollback.
    assert session.query(LinkedInQueue).filter_by(id=queue_id).first() is None


def test_queue_legacy_helper_still_commits(session):
    """The legacy committing helper is preserved for non-orch callers."""
    from src.services.linkedin_batch import queue_linkedin_action

    queue_id = queue_linkedin_action(
        db=session,
        prospect_id="lead-1",
        campaign_id=None,
        action_type="connect",
        linkedin_url="https://linkedin.com/in/y",
        message_text="Hi",
    )
    session.rollback()  # no-op because it was already committed
    assert session.query(LinkedInQueue).filter_by(id=queue_id).first() is not None


def test_connect_handler_refuses_without_parent_session(session):
    """P2-4 fail-closed: handler must not open a fresh session."""
    from src.orchestration.enums import StepResultStatus, StepType
    from src.orchestration.handlers import SendLinkedInConnectHandler
    from src.orchestration.models import LeadContext, LeadSequenceState, SequenceStep
    from src.orchestration.repositories import (
        ActionExecutionRepository,
        AuditLogRepository,
        ProspectRepository,
    )
    from src.orchestration.enums import OrchestrationStatus

    handler = SendLinkedInConnectHandler(
        linkedin_provider=object(),  # not StubLinkedInProvider
        lead_repo=ProspectRepository(session),
        action_repo=ActionExecutionRepository(session),
        audit_repo=AuditLogRepository(session),
        db=None,  # <-- the bug path
    )
    lead = LeadContext(
        lead_id="lead-1",
        email="x@x.com",
        first_name="X",
        last_name="Y",
        company="Acme",
        title="HSE",
        industry="",
        location="",
        linkedin_url="https://linkedin.com/in/x",
        linkedin_connected=False,
    )
    state = LeadSequenceState(
        state_id="state-1",
        lead_id="lead-1",
        sequence_id="seq",
        status=OrchestrationStatus.active,
        current_step_index=0,
        enrolled_at=datetime.now(timezone.utc),
    )
    step = SequenceStep(
        step_id="li_connect",
        step_type=StepType.send_linkedin_connect,
        day_offset=0,
        note_template_id="note",
    )

    result = handler.handle(step=step, lead=lead, state=state, execution_id="exec-1")

    assert result.status == StepResultStatus.failed
    assert "parent session missing" in (result.detail or "")
    # No queue row should have been created
    assert session.query(LinkedInQueue).count() == 0


def test_message_handler_refuses_without_parent_session(session):
    """P2-4 fail-closed: same guard on the message handler."""
    from src.orchestration.enums import StepResultStatus, StepType, OrchestrationStatus
    from src.orchestration.handlers import SendLinkedInMessageHandler
    from src.orchestration.models import LeadContext, LeadSequenceState, SequenceStep
    from src.orchestration.repositories import (
        ActionExecutionRepository,
        AuditLogRepository,
        ManualTaskRepository,
    )

    handler = SendLinkedInMessageHandler(
        linkedin_provider=object(),
        action_repo=ActionExecutionRepository(session),
        task_repo=ManualTaskRepository(session),
        audit_repo=AuditLogRepository(session),
        db=None,
    )
    lead = LeadContext(
        lead_id="lead-1",
        email="x@x.com",
        first_name="X",
        last_name="Y",
        company="Acme",
        title="HSE",
        industry="",
        location="",
        linkedin_url="https://linkedin.com/in/x",
        linkedin_connected=True,  # pass the not-connected gate
    )
    state = LeadSequenceState(
        state_id="state-1",
        lead_id="lead-1",
        sequence_id="seq",
        status=OrchestrationStatus.active,
        current_step_index=0,
        enrolled_at=datetime.now(timezone.utc),
    )
    step = SequenceStep(
        step_id="li_msg",
        step_type=StepType.send_linkedin_message,
        day_offset=0,
        template_id="tmpl",
    )

    result = handler.handle(step=step, lead=lead, state=state, execution_id="exec-1")
    assert result.status == StepResultStatus.failed
    assert "parent session missing" in (result.detail or "")
    assert session.query(LinkedInQueue).count() == 0


def test_connect_handler_with_parent_session_queues_and_rolls_back(session):
    """
    End-to-end P2-4 assertion: when the handler runs with a parent session
    and the caller then rolls back (simulating a scheduler commit failure),
    the LinkedIn queue row is rolled back too - no split-brain.
    """
    from src.orchestration.enums import StepResultStatus, StepType, OrchestrationStatus
    from src.orchestration.handlers import SendLinkedInConnectHandler
    from src.orchestration.models import LeadContext, LeadSequenceState, SequenceStep
    from src.orchestration.repositories import (
        ActionExecutionRepository,
        AuditLogRepository,
        ProspectRepository,
    )

    handler = SendLinkedInConnectHandler(
        linkedin_provider=object(),
        lead_repo=ProspectRepository(session),
        action_repo=ActionExecutionRepository(session),
        audit_repo=AuditLogRepository(session),
        db=session,  # P2-4: parent session injected
    )
    lead = LeadContext(
        lead_id="lead-1",
        email="x@x.com",
        first_name="X",
        last_name="Y",
        company="Acme",
        title="HSE",
        industry="",
        location="",
        linkedin_url="https://linkedin.com/in/x",
        linkedin_connected=False,
    )
    state = LeadSequenceState(
        state_id="state-1",
        lead_id="lead-1",
        sequence_id="seq",
        status=OrchestrationStatus.active,
        current_step_index=0,
        enrolled_at=datetime.now(timezone.utc),
    )
    step = SequenceStep(
        step_id="li_connect",
        step_type=StepType.send_linkedin_connect,
        day_offset=0,
        note_template_id="note",
    )

    result = handler.handle(step=step, lead=lead, state=state, execution_id="exec-roll")
    assert result.status == StepResultStatus.executed
    assert session.query(LinkedInQueue).count() == 1

    # Simulate the scheduler's commit failing → rollback.
    session.rollback()
    assert session.query(LinkedInQueue).count() == 0
