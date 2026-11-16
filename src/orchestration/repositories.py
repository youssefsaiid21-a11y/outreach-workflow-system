"""
Repository classes that abstract all database access for the orchestration layer.

This is the data access layer (DAL) between the orchestration business logic
and the database. Every DB query in the orchestration engine goes through one
of these repository classes.

REPOSITORIES:
  - LeadSequenceStateRepository: CRUD for orch_sequence_states (the FSM state table)
  - EngagementEventRepository: read/write for orch_engagement_events (webhook audit log)
  - ActionExecutionRepository: read/write for orch_action_executions (step attempt records)
  - ManualTaskRepository: CRUD for orch_manual_tasks (human-action items)
  - AuditLogRepository: append-only writes to orch_audit_logs (state transition history)
  - ProspectRepository: read/update for the main prospects table (shared with the app)

TRANSACTION BOUNDARY:
  Each repository takes a SQLAlchemy Session in its constructor. Repositories
  never commit or roll back on their own — the caller controls the transaction.
  This is critical because the scheduler, dispatcher, and handlers all share
  the same session so changes commit atomically.

ALSO EXPORTS:
  - _orm_to_state(): converts ORM row → Pydantic LeadSequenceState
  - build_execution_id(): deterministic execution_id for idempotency

Pattern:
    with SessionLocal() as db:
        repo = LeadSequenceStateRepository(db)
        state = repo.get("some-state-id")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from ..db.models import Prospect
from .enums import EventType, OrchestrationStatus, TaskStatus
from .models import (
    ActionExecution,
    AuditLog,
    EngagementEvent,
    LeadSequenceState,
    ManualTask,
)
from .orm import (
    ActionExecutionORM,
    AuditLogORM,
    EngagementEventORM,
    ManualTaskORM,
    OrchSequenceStateORM,
)

logger = logging.getLogger(__name__)

# Statuses that indicate a sequence is still in progress (non-terminal).
_ACTIVE_STATUSES = (
    OrchestrationStatus.active.value,
    OrchestrationStatus.waiting_for_next_step.value,
    OrchestrationStatus.paused.value,
)

_TERMINAL_STATUSES = (
    OrchestrationStatus.stopped_positive_reply.value,
    OrchestrationStatus.stopped_negative_reply.value,
    OrchestrationStatus.stopped_unsubscribed.value,
    OrchestrationStatus.stopped_bounced.value,
    OrchestrationStatus.stopped_disqualified.value,
    OrchestrationStatus.completed.value,
)


# ---------------------------------------------------------------------------
# Helper: ORM → Pydantic
# ---------------------------------------------------------------------------


def _orm_to_state(row: OrchSequenceStateORM) -> LeadSequenceState:
    """Convert an ORM row to a LeadSequenceState Pydantic model."""
    return LeadSequenceState(
        state_id=row.state_id,
        lead_id=row.lead_id,
        sequence_id=row.sequence_id,
        status=OrchestrationStatus(row.status),
        current_step_index=row.current_step_index,
        enrolled_at=row.enrolled_at,
        scheduled_next_at=row.scheduled_next_at,
        engagement_score=row.engagement_score,
        stop_reason=row.stop_reason,
        paused_reason=row.paused_reason,
        completed_steps=row.completed_steps or [],
        # P2-2: retry ladder counter  -  defaults to 0 if column missing on
        # legacy rows (alembic migration backfills NULL → 0).
        consecutive_failure_count=getattr(row, "consecutive_failure_count", 0) or 0,
        # Optimistic locking version
        version=getattr(row, "version", 0) or 0,
    )


# ---------------------------------------------------------------------------
# LeadSequenceStateRepository
# ---------------------------------------------------------------------------


class LeadSequenceStateRepository:
    """Data access for orch_sequence_states."""

    def __init__(self, db: Session) -> None:
        """
        Args:
            db: SQLAlchemy session  -  caller controls the transaction.
        """
        self.db = db

    def get(self, state_id: str) -> OrchSequenceStateORM | None:
        """
        Retrieve a single sequence state by its primary key.

        Args:
            state_id: UUID string of the state record.

        Returns:
            ORM row or None if not found.
        """
        return (
            self.db.query(OrchSequenceStateORM)
            .filter(OrchSequenceStateORM.state_id == state_id)
            .first()
        )

    def get_by_lead(self, lead_id: str) -> list[OrchSequenceStateORM]:
        """
        Return all sequence states for a lead (including historical / completed).

        Args:
            lead_id: Internal lead identifier.

        Returns:
            List of ORM rows ordered by enrolled_at descending.
        """
        return (
            self.db.query(OrchSequenceStateORM)
            .filter(OrchSequenceStateORM.lead_id == lead_id)
            .order_by(OrchSequenceStateORM.enrolled_at.desc())
            .all()
        )

    def get_active_for_lead(self, lead_id: str) -> OrchSequenceStateORM | None:
        """
        Return the non-terminal sequence state for a lead, if any.

        Only one non-terminal state is expected per lead at any time.  If
        multiple exist (data integrity issue) the most recently enrolled one
        is returned.

        Args:
            lead_id: Internal lead identifier.

        Returns:
            ORM row or None.
        """
        return (
            self.db.query(OrchSequenceStateORM)
            .filter(
                OrchSequenceStateORM.lead_id == lead_id,
                OrchSequenceStateORM.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(OrchSequenceStateORM.enrolled_at.desc())
            .first()
        )

    def get_all(self, include_terminal: bool = False) -> list[OrchSequenceStateORM]:
        """
        Return all sequence states, newest first.

        Args:
            include_terminal: If True, include completed/stopped states too.

        Returns:
            List of ORM rows ordered by enrolled_at descending.
        """
        q = self.db.query(OrchSequenceStateORM)
        if not include_terminal:
            q = q.filter(OrchSequenceStateORM.status.in_(_ACTIVE_STATUSES))
        return q.order_by(OrchSequenceStateORM.enrolled_at.desc()).all()

    def get_due(
        self,
        now: datetime,
        limit: int | None = None,
        user_id: str | None = None,
    ) -> list[OrchSequenceStateORM]:
        """
        Return sequence states where a step is due for execution.

        A state is due when:
        - status is active or waiting_for_next_step
        - scheduled_next_at is not null and <= now
        - P3-4: optionally, the row's ``user_id`` matches the filter

        P2-6 / deep audit MEDIUM-3: on Postgres we acquire row-level locks
        with `SELECT ... FOR UPDATE SKIP LOCKED` so two concurrent scheduler
        runs cannot double-process the same lead. SQLite has no equivalent
         -  the test suite uses an in-process scheduler so there is no real
        race window.

        P3-4 / deep audit CRITICAL-3: when ``user_id`` is passed, the query
        is scoped to that user's sequence states. The background scheduler
        loop iterates active users and calls this once per user so each
        tick processes work against that user's own provider credentials.
        ``user_id=None`` keeps the legacy "process everything" behaviour
        used by the HTTP ``/scheduler/run`` admin endpoint and the test
        suite.

        Args:
            now:     Current UTC datetime used as the cutoff.
            limit:   Optional cap on the number of rows returned (batch limit).
            user_id: Optional  -  when set, only return states owned by this
                     user.

        Returns:
            List of ORM rows. Caller must commit / rollback to release the
            row locks (Postgres).
        """
        q = (
            self.db.query(OrchSequenceStateORM)
            .filter(
                OrchSequenceStateORM.status.in_(
                    (
                        OrchestrationStatus.active.value,
                        OrchestrationStatus.waiting_for_next_step.value,
                    )
                ),
                OrchSequenceStateORM.scheduled_next_at.isnot(None),
                OrchSequenceStateORM.scheduled_next_at <= now,
            )
        )
        if user_id is not None:
            # The user_id column exists from alembic 0003 (P3-1) and is
            # backfilled for every pre-migration row. The filter is a
            # simple column equality  -  no join  -  so Postgres can use the
            # ix_orch_seq_states_user_id index.
            q = q.filter(OrchSequenceStateORM.user_id == user_id)
        # Process oldest-due first so the batch cap doesn't starve long-waiting states.
        q = q.order_by(OrchSequenceStateORM.scheduled_next_at.asc())
        if limit is not None:
            q = q.limit(limit)

        # P2-6: pessimistic locking on Postgres only.
        try:
            dialect_name = self.db.bind.dialect.name if self.db.bind is not None else ""
        except Exception:
            dialect_name = ""
        if dialect_name == "postgresql":
            q = q.with_for_update(skip_locked=True)

        return q.all()

    def save(self, state: LeadSequenceState) -> OrchSequenceStateORM:
        """
        Upsert a LeadSequenceState Pydantic model to the database.

        Creates a new row if state_id doesn't exist, otherwise updates the
        existing row.

        Args:
            state: Pydantic model to persist.

        Returns:
            The persisted ORM row.
        """
        row = self.get(state.state_id)
        if row is None:
            row = OrchSequenceStateORM(state_id=state.state_id)
            self.db.add(row)

        row.lead_id = state.lead_id
        row.sequence_id = state.sequence_id
        row.status = state.status.value
        row.current_step_index = state.current_step_index
        row.enrolled_at = state.enrolled_at
        row.scheduled_next_at = state.scheduled_next_at
        row.engagement_score = state.engagement_score
        row.stop_reason = state.stop_reason
        row.paused_reason = state.paused_reason
        row.completed_steps = state.completed_steps
        # P2-2: retry ladder counter
        row.consecutive_failure_count = state.consecutive_failure_count
        # Optimistic locking: do NOT manually set row.version here.
        # SQLAlchemy's version_id_col (__mapper_args__) auto-increments
        # it on flush and adds WHERE version = :old to every UPDATE.
        # Manually overwriting it with a (potentially stale) Pydantic
        # value defeats the whole mechanism.  (Audit 2026-05-05 #1)
        row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)  # Fix 12

        if not row.created_at:
            row.created_at = datetime.now(timezone.utc).replace(tzinfo=None)  # Fix 12

        self.db.flush()
        return row

    def upsert_from_pydantic(self, state: LeadSequenceState) -> None:
        """
        Convenience wrapper around save() that discards the return value.

        Args:
            state: Pydantic model to persist.
        """
        self.save(state)


# ---------------------------------------------------------------------------
# EngagementEventRepository
# ---------------------------------------------------------------------------


class EngagementEventRepository:
    """Data access for orch_engagement_events."""

    def __init__(self, db: Session) -> None:
        """
        Args:
            db: SQLAlchemy session.
        """
        self.db = db

    def save(self, event: EngagementEvent) -> EngagementEventORM:
        """
        Persist a new EngagementEvent.

        Args:
            event: Pydantic event model.

        Returns:
            The persisted ORM row.
        """
        row = EngagementEventORM(
            event_id=event.event_id,
            lead_id=event.lead_id,
            event_type=event.event_type.value,
            occurred_at=event.occurred_at,
            ingested_at=event.ingested_at,
            event_metadata=event.metadata,
            raw_payload=event.raw_payload,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def get_by_lead(
        self,
        lead_id: str,
        event_type: EventType | None = None,
    ) -> list[EngagementEventORM]:
        """
        Return all engagement events for a lead, optionally filtered by type.

        Args:
            lead_id:    Internal lead identifier.
            event_type: Optional filter; if None all event types are returned.

        Returns:
            List of ORM rows ordered by occurred_at ascending.
        """
        q = self.db.query(EngagementEventORM).filter(
            EngagementEventORM.lead_id == lead_id
        )
        if event_type is not None:
            q = q.filter(EngagementEventORM.event_type == event_type.value)
        return q.order_by(EngagementEventORM.occurred_at.asc()).all()

    def count_opens(self, lead_id: str) -> int:
        """
        Count the number of email_opened events for a lead.

        Note: single opens are not counted as engagement points in the scorer
        because open tracking is unreliable.  This count is used to detect
        repeat-open behaviour (>= 3).

        Args:
            lead_id: Internal lead identifier.

        Returns:
            Integer count of email_opened events.
        """
        return (
            self.db.query(EngagementEventORM)
            .filter(
                EngagementEventORM.lead_id == lead_id,
                EngagementEventORM.event_type == EventType.email_opened.value,
            )
            .count()
        )

    def count_distinct_click_sessions(self, lead_id: str) -> int:
        """
        Count the number of email_clicked events for a lead.

        Each row is treated as a distinct click session.  Deduplication of
        rapid clicks within the same 5-minute window is handled at ingestion
        time so this count reflects unique sessions.

        Args:
            lead_id: Internal lead identifier.

        Returns:
            Integer count of email_clicked events.
        """
        return (
            self.db.query(EngagementEventORM)
            .filter(
                EngagementEventORM.lead_id == lead_id,
                EngagementEventORM.event_type == EventType.email_clicked.value,
            )
            .count()
        )


# ---------------------------------------------------------------------------
# ActionExecutionRepository
# ---------------------------------------------------------------------------


def build_execution_id(state_id: str, step_id: str, attempt: int) -> str:
    """
    Build a deterministic execution_id for a (state, step, attempt) triple.

    P2-7 / deep audit HIGH-6: single source of truth for execution_id
    construction. Previously `scheduler.execute_next_step` always used
    `_1` while `dispatcher.dispatch` used `_{count_attempts+1}`  -  the two
    disagreed, and retry semantics produced orphan execution rows with
    inconsistent ids. Both call sites now build ids through this helper.
    """
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    return f"{state_id}_{step_id}_{attempt}"


class ActionExecutionRepository:
    """Data access for orch_action_executions."""

    def __init__(self, db: Session) -> None:
        """
        Args:
            db: SQLAlchemy session.
        """
        self.db = db

    def exists(self, execution_id: str) -> bool:
        """
        Check whether an execution row already exists for this idempotency key.

        P2-7 / deep audit HIGH-6: failed rows are INCLUDED in this check so
        that a retry using the SAME `execution_id` is treated as idempotent
        and skipped. Retries must use a fresh `execution_id` built from a
        higher attempt number via `build_execution_id()`. This is the
        single rule that scheduler and dispatcher both obey now.

        Args:
            execution_id: The execution / idempotency key.

        Returns:
            True if any record (including failed) already exists.
        """
        row = self.db.query(ActionExecutionORM).filter(
            ActionExecutionORM.execution_id == execution_id,
        ).first()
        return row is not None

    def save(self, execution: ActionExecution) -> ActionExecutionORM:
        """
        Persist an ActionExecution record.

        Args:
            execution: Pydantic model.

        Returns:
            The persisted ORM row.
        """
        row = ActionExecutionORM(
            execution_id=execution.execution_id,
            lead_id=execution.lead_id,
            step_id=execution.step_id,
            action_type=execution.action_type.value,
            status=execution.status.value,
            attempted_at=execution.attempted_at,
            result_payload=execution.result_payload,
            error_message=execution.error_message,
            state_id=execution.state_id,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def count_attempts(self, state_id: str, step_id: str) -> int:
        """
        Count ALL execution attempts (including failed) for a (state, step) pair.

        P2-7 / deep audit HIGH-6: failed rows are now counted so the next
        retry's `attempt` value strictly increments, producing a fresh
        `execution_id` via `build_execution_id()` instead of colliding
        with the failed row.
        """
        return (
            self.db.query(ActionExecutionORM)
            .filter(
                ActionExecutionORM.state_id == state_id,
                ActionExecutionORM.step_id == step_id,
            )
            .count()
        )

    def get_by_lead(self, lead_id: str) -> list[ActionExecutionORM]:
        """
        Return all action executions for a lead.

        Args:
            lead_id: Internal lead identifier.

        Returns:
            List of ORM rows ordered by attempted_at ascending.
        """
        return (
            self.db.query(ActionExecutionORM)
            .filter(ActionExecutionORM.lead_id == lead_id)
            .order_by(ActionExecutionORM.attempted_at.asc())
            .all()
        )

    def get_by_state(self, state_id: str) -> list[ActionExecutionORM]:
        """
        Return all action executions for a specific sequence state.

        Args:
            state_id: UUID of the OrchSequenceStateORM row.

        Returns:
            List of ORM rows ordered by attempted_at ascending.
        """
        return (
            self.db.query(ActionExecutionORM)
            .filter(ActionExecutionORM.state_id == state_id)
            .order_by(ActionExecutionORM.attempted_at.asc())
            .all()
        )


# ---------------------------------------------------------------------------
# ManualTaskRepository
# ---------------------------------------------------------------------------


class ManualTaskRepository:
    """Data access for orch_manual_tasks."""

    def __init__(self, db: Session) -> None:
        """
        Args:
            db: SQLAlchemy session.
        """
        self.db = db

    def save(self, task: ManualTask) -> ManualTaskORM:
        """
        Persist a new ManualTask.

        Args:
            task: Pydantic model.

        Returns:
            The persisted ORM row.
        """
        row = ManualTaskORM(
            task_id=task.task_id,
            lead_id=task.lead_id,
            account_id=task.account_id,
            reason=task.reason,
            priority=task.priority.value,
            suggested_action=task.suggested_action,
            due_date=task.due_date,
            status=task.status.value,
            created_at=task.created_at,
            updated_at=task.created_at,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def get_open_tasks(
        self, priority: str | None = None
    ) -> list[ManualTaskORM]:
        """
        Return all open (non-terminal) manual tasks, optionally filtered by priority.

        Args:
            priority: Optional priority string filter (e.g. "urgent", "high").

        Returns:
            List of ORM rows ordered by created_at ascending.
        """
        q = self.db.query(ManualTaskORM).filter(
            ManualTaskORM.status.in_([TaskStatus.open.value, TaskStatus.in_progress.value])
        )
        if priority is not None:
            q = q.filter(ManualTaskORM.priority == priority)
        return q.order_by(ManualTaskORM.created_at.asc()).all()

    def get_by_lead(self, lead_id: str) -> list[ManualTaskORM]:
        """
        Return all manual tasks for a lead.

        Args:
            lead_id: Internal lead identifier.

        Returns:
            List of ORM rows ordered by created_at descending.
        """
        return (
            self.db.query(ManualTaskORM)
            .filter(ManualTaskORM.lead_id == lead_id)
            .order_by(ManualTaskORM.created_at.desc())
            .all()
        )

    def update_status(
        self, task_id: str, status: TaskStatus
    ) -> ManualTaskORM | None:
        """
        Update the status of a manual task.

        Args:
            task_id: UUID of the task to update.
            status:  New TaskStatus value.

        Returns:
            Updated ORM row, or None if not found.
        """
        row = (
            self.db.query(ManualTaskORM)
            .filter(ManualTaskORM.task_id == task_id)
            .first()
        )
        if row is None:
            return None
        row.status = status.value
        row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)  # Fix 12
        self.db.flush()
        return row


# ---------------------------------------------------------------------------
# AuditLogRepository
# ---------------------------------------------------------------------------


class AuditLogRepository:
    """Data access for orch_audit_logs."""

    def __init__(self, db: Session) -> None:
        """
        Args:
            db: SQLAlchemy session.
        """
        self.db = db

    def write(self, log: AuditLog) -> AuditLogORM:
        """
        Append a new audit log entry.

        Args:
            log: Pydantic AuditLog model.

        Returns:
            The persisted ORM row.
        """
        row = AuditLogORM(
            log_id=log.log_id,
            lead_id=log.lead_id,
            state_id=log.state_id,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            action=log.action,
            from_status=log.from_status,
            to_status=log.to_status,
            trigger=log.trigger,
            detail=log.detail,
            created_at=log.created_at,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def get_by_lead(self, lead_id: str) -> list[AuditLogORM]:
        """
        Return all audit entries for a lead.

        Args:
            lead_id: Internal lead identifier.

        Returns:
            List of ORM rows ordered by created_at ascending.
        """
        return (
            self.db.query(AuditLogORM)
            .filter(AuditLogORM.lead_id == lead_id)
            .order_by(AuditLogORM.created_at.asc())
            .all()
        )

    def get_by_state(self, state_id: str) -> list[AuditLogORM]:
        """
        Return all audit entries for a specific sequence state.

        Args:
            state_id: UUID of the OrchSequenceStateORM row.

        Returns:
            List of ORM rows ordered by created_at ascending.
        """
        return (
            self.db.query(AuditLogORM)
            .filter(AuditLogORM.state_id == state_id)
            .order_by(AuditLogORM.created_at.asc())
            .all()
        )


# ---------------------------------------------------------------------------
# ProspectRepository
# ---------------------------------------------------------------------------


class ProspectRepository:
    """Data access for the existing prospects table."""

    def __init__(self, db: Session) -> None:
        """
        Args:
            db: SQLAlchemy session.
        """
        self.db = db

    def get(self, lead_id: str) -> Prospect | None:
        """
        Retrieve a Prospect by its primary key.

        Args:
            lead_id: Prospect.id value.

        Returns:
            Prospect ORM row or None.
        """
        return (
            self.db.query(Prospect)
            .filter(Prospect.id == lead_id)
            .first()
        )

    def get_by_email(self, email: str) -> Prospect | None:
        """
        Retrieve a Prospect by email address.

        Args:
            email: Email address to look up.

        Returns:
            Prospect ORM row or None.
        """
        return (
            self.db.query(Prospect)
            .filter(Prospect.email == email)
            .first()
        )

    def update_linkedin_connected(
        self,
        lead_id: str,
        connected: bool,
        connect_sent_at: datetime | None = None,
    ) -> None:
        """
        Update the LinkedIn connection status for a prospect (Fix 21).

        Persists to linkedin_connected and linkedin_connect_sent_at columns
        added via migration in database.py.

        Args:
            lead_id:          Internal lead identifier.
            connected:        True if the connection was accepted.
            connect_sent_at:  When the connect request was first sent.
        """
        row = self.db.query(Prospect).filter_by(id=lead_id).first()
        if row:
            try:
                row.linkedin_connected = connected
                if connect_sent_at is not None:
                    row.linkedin_connect_sent_at = connect_sent_at
                self.db.flush()
                logger.info(
                    "ProspectRepository.update_linkedin_connected: lead=%s connected=%s sent_at=%s",
                    lead_id,
                    connected,
                    connect_sent_at,
                )
            except Exception as exc:
                logger.warning(
                    "ProspectRepository.update_linkedin_connected: could not persist for lead=%s: %s",
                    lead_id,
                    exc,
                )
        else:
            logger.warning(
                "ProspectRepository.update_linkedin_connected: lead=%s not found", lead_id
            )
