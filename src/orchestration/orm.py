"""
SQLAlchemy ORM models for orchestration-specific database tables.

These ORM models define the physical database schema for the orchestration
engine. They map to five tables:

  - orch_sequence_states: where each lead sits in their sequence (the FSM state)
  - orch_engagement_events: immutable audit log of every webhook/signal received
  - orch_action_executions: record of every step execution attempt (idempotency keys)
  - orch_manual_tasks: human-action items surfaced to the sales rep
  - orch_audit_logs: append-only audit trail of every state transition

These models extend the shared Base from src.db.database (same metadata as
the main app's Prospect, Campaign, EmailSequence tables). They are NOT
re-definitions of existing tables — only orchestration-specific tables.

RELATIONSHIP TO PYDANTIC MODELS (models.py):
  - ORM models = database schema + SQLAlchemy session state
  - Pydantic models = domain objects used by business logic
  - Conversion happens in repositories.py (_orm_to_state, save/upsert methods)

IMPORTANT: Import this module before calling Base.metadata.create_all() so
that SQLAlchemy registers the tables.
"""

from datetime import datetime, timezone


def _utcnow():
    """Return current UTC datetime as a naive datetime (SQLite-compatible). Fix 12."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)

from ..db.database import Base


class OrchSequenceStateORM(Base):
    """
    Tracks where a single lead sits inside a sequence at any given moment.

    One row per (lead, sequence) pair.  Multiple rows are possible if a lead
    is ever re-enrolled after completing a sequence.
    """

    __tablename__ = "orch_sequence_states"

    state_id = Column(String, primary_key=True)
    lead_id = Column(
        String,
        ForeignKey("prospects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # P3-1 / deep audit CRITICAL-3: multi-tenant scoping column. Nullable so
    # the alembic 0003 migration can add it on prod without a table rewrite;
    # backfilled from ``prospects.user_id`` via the same migration. Indexed
    # via ``ix_orch_seq_states_user_id`` for the per-user scheduler filter.
    user_id = Column(String, nullable=True, index=True)
    sequence_id = Column(String, nullable=False)
    status = Column(String, nullable=False, index=True)
    current_step_index = Column(Integer, default=0, nullable=False)
    enrolled_at = Column(DateTime, nullable=False)
    scheduled_next_at = Column(DateTime, nullable=True)
    engagement_score = Column(Integer, default=0, nullable=False)
    stop_reason = Column(String, nullable=True)
    paused_reason = Column(String, nullable=True)
    # JSON list of step_ids that have been executed
    completed_steps = Column(JSON, default=list, nullable=False)
    # P2-2 / deep audit HIGH-4: retry ladder counter for transient failures
    consecutive_failure_count = Column(Integer, default=0, nullable=False)
    # Optimistic locking: SQLAlchemy auto-increments on flush and adds
    # ``WHERE version = :old`` to every UPDATE. Concurrent writers get
    # ``StaleDataError`` instead of silently overwriting each other.
    version = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    # Optimistic locking: SQLAlchemy auto-increments `version` on every flush
    # and adds WHERE version = :old to every UPDATE. If two writers (scheduler
    # tick + webhook ingestion) race on the same row, the slower one gets a
    # StaleDataError instead of silently overwriting the faster one's changes.
    __mapper_args__ = {"version_id_col": version}

    __table_args__ = (
        # Composite index for the "find active state for a lead" query
        Index("ix_orch_seq_states_lead_status", "lead_id", "status"),
        # Index for the scheduler's "find all due states" query (ORDER BY scheduled_next_at)
        Index("ix_orch_seq_states_scheduled", "scheduled_next_at"),
    )


class EngagementEventORM(Base):
    """
    Immutable record of every engagement signal received for a lead.

    Events are written once and never mutated  -  the state machine reads them
    to derive the current state but does not update them.
    """

    __tablename__ = "orch_engagement_events"

    event_id = Column(String, primary_key=True)
    lead_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    occurred_at = Column(DateTime, nullable=False, index=True)
    ingested_at = Column(DateTime, nullable=False)
    # Typed metadata extracted by the ingestion layer (e.g. {"template_id": "..."})
    event_metadata = Column(JSON, default=dict, nullable=False)
    # Full raw webhook payload preserved for debugging / replay
    raw_payload = Column(JSON, default=dict, nullable=False)

    __table_args__ = (
        Index("ix_orch_events_lead_type", "lead_id", "event_type"),
    )


class ActionExecutionORM(Base):
    """
    Audit record for every attempt to execute a sequence step.

    The execution_id doubles as an idempotency key so the dispatcher can
    detect and skip duplicate executions caused by retries.
    """

    __tablename__ = "orch_action_executions"

    execution_id = Column(String, primary_key=True)
    lead_id = Column(String, nullable=False, index=True)
    step_id = Column(String, nullable=False)
    action_type = Column(String, nullable=False)
    status = Column(String, nullable=False)
    attempted_at = Column(DateTime, nullable=False)
    result_payload = Column(JSON, nullable=True)
    error_message = Column(String, nullable=True)
    state_id = Column(String, nullable=False, index=True)

    __table_args__ = (
        Index("ix_orch_executions_state_step", "state_id", "step_id"),
    )


class ManualTaskORM(Base):
    """
    A task surfaced to the sales rep when human judgement is required.

    Tasks are created by the orchestration layer (e.g. high-score lead,
    LinkedIn fallback, key-account bounce) and resolved by the rep via the UI.
    """

    __tablename__ = "orch_manual_tasks"

    task_id = Column(String, primary_key=True)
    lead_id = Column(String, nullable=False, index=True)
    account_id = Column(String, nullable=True)
    reason = Column(String, nullable=False)
    priority = Column(String, nullable=False, index=True)
    suggested_action = Column(Text, nullable=False)
    due_date = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="open", index=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_orch_tasks_lead_status", "lead_id", "status"),
    )


class AuditLogORM(Base):
    """
    Append-only audit trail for every state transition and significant action.

    Never updated  -  only inserted.  Used for debugging, compliance, and
    building timeline views in the UI.
    """

    __tablename__ = "orch_audit_logs"

    log_id = Column(String, primary_key=True)
    lead_id = Column(String, nullable=True, index=True)
    state_id = Column(String, nullable=True)
    entity_type = Column(String, nullable=False)   # e.g. "sequence_state", "task"
    entity_id = Column(String, nullable=False)
    action = Column(String, nullable=False)        # e.g. "status_transition"
    from_status = Column(String, nullable=True)
    to_status = Column(String, nullable=True)
    trigger = Column(String, nullable=True)        # e.g. "event_replied_positive"
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_orch_audit_lead_created", "lead_id", "created_at"),
        Index("ix_orch_audit_state", "state_id"),
    )
