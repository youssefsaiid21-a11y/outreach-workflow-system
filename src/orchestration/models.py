"""
Pydantic v2 data models for the orchestration layer.

This file defines the domain models that flow through the entire orchestration
pipeline. The separation from ORM models (see orm.py) is intentional:

  - ORM models (orm.py) map 1:1 to database tables and carry SQLAlchemy state
  - Pydantic models (this file) are pure data with validation, used by the
    state machine, scheduler, dispatcher, and handlers

Conversion between the two layers happens in repositories.py (_orm_to_state,
save/upsert methods).

Key models and where they're used:
  - SequenceConfig / SequenceStep → config.py defines sequences, scheduler reads them
  - LeadContext → snapshot passed to handlers so they never query the DB
  - LeadSequenceState → the FSM state; mutated by state_machine.py, persisted by repositories.py
  - EngagementEvent → immutable webhook record, created by ingestion.py
  - ActionExecution → audit record per step attempt, created by handlers via _save_execution
  - StepResult → returned by handlers to the dispatcher to indicate outcome
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    EventType,
    OrchestrationStatus,
    StepResultStatus,
    StepType,
    TaskPriority,
    TaskStatus,
)


def _new_uuid() -> str:
    """Generate a new UUID4 string.  Used as a default_factory for ID fields."""
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class StepCondition(BaseModel):
    """A single condition that must be satisfied before a step executes."""

    model_config = ConfigDict(populate_by_name=True)

    condition_type: str = Field(
        ...,
        description=(
            "Type identifier, e.g. 'no_reply', 'linkedin_connected', "
            "'linkedin_url_exists', 'no_unsubscribe'."
        ),
    )
    value: str | None = Field(
        default=None,
        description="Optional comparison value; None means the condition is boolean.",
    )


class SequenceStep(BaseModel):
    """
    A single step inside an outreach sequence.

    Steps are ordered by day_offset from the enrolment date.  The step_id
    must be stable across restarts (use uuid5 with a deterministic seed in
    config.py) so that idempotency checks work correctly.
    """

    model_config = ConfigDict(populate_by_name=True)

    step_id: str = Field(default_factory=_new_uuid)
    step_type: StepType
    day_offset: int = Field(..., ge=0, description="Days after enrolment when this step is due.")
    template_id: str | None = Field(default=None, description="Email template identifier.")
    conditions: list[StepCondition] = Field(default_factory=list)
    enabled: bool = Field(default=True)
    note_template_id: str | None = Field(
        default=None,
        description="LinkedIn connection note template identifier.",
    )
    variant_id: str | None = Field(
        default=None,
        description="A/B test variant identifier (stub  -  not yet active).",
    )
    label: str | None = Field(
        default=None,
        description="Human-readable label for email steps (e.g. 'Problem Intro', 'Pattern Break'). Used in prompt generation.",
    )


class SequenceConfig(BaseModel):
    """
    Full definition of an outreach sequence.

    A SequenceConfig is immutable once published.  Changes require a new
    sequence_id so that in-flight leads on the old config are not disrupted.
    """

    model_config = ConfigDict(populate_by_name=True)

    sequence_id: str = Field(default_factory=_new_uuid)
    name: str
    steps: list[SequenceStep]
    scoring_threshold: int = Field(
        default=5,
        description="Engagement score at which a manual task is created.",
    )
    business_days_only: bool = Field(
        default=False,
        description="Skip scheduling on weekends when True.",
    )
    timezone: str = Field(
        default="Asia/Riyadh",
        description="Timezone used when evaluating business_days_only.",
    )
    daily_send_limit: int | None = Field(
        default=None,
        description="Max send_email actions per day for this sequence. None = use system default.",
    )


# ---------------------------------------------------------------------------
# Lead context
# ---------------------------------------------------------------------------


class LeadContext(BaseModel):
    """
    Snapshot of a lead's properties at the time a step executes.

    This model is constructed from the Prospect ORM + orchestration state and
    passed to handlers so they never need to query the database themselves.
    """

    model_config = ConfigDict(populate_by_name=True)

    lead_id: str
    email: str
    first_name: str
    last_name: str
    company: str
    title: str
    industry: str
    location: str
    linkedin_url: str | None = None
    linkedin_connected: bool = False
    linkedin_connect_sent_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    is_key_account: bool = False

    @model_validator(mode='before')
    @classmethod
    def derive_key_account(cls, values: dict) -> dict:
        """Derive is_key_account from tags if not explicitly set to True (Fix 4)."""
        tags = values.get('tags', [])
        if not values.get('is_key_account', False):
            values['is_key_account'] = 'key_account' in (tags or [])
        return values


# ---------------------------------------------------------------------------
# Sequence state
# ---------------------------------------------------------------------------


class LeadSequenceState(BaseModel):
    """
    The current position and lifecycle status of a lead inside a sequence.

    This is the core FSM state object. It tracks:
    - Where the lead is in the sequence (current_step_index)
    - What status the lead is in (pending → active → waiting → completed/stopped)
    - When the next step should fire (scheduled_next_at)
    - How engaged the lead is (engagement_score)
    - Whether/why the sequence was stopped or paused

    Persisted to orch_sequence_states via OrchSequenceStateORM.
    The state_machine.py functions mutate this object in place; repositories.py
    persists changes to the DB.

    One lead can have multiple state rows (e.g., re-enrolled after completing),
    but only one should be non-terminal at any time.
    """

    model_config = ConfigDict(populate_by_name=True)

    state_id: str = Field(default_factory=_new_uuid)
    lead_id: str
    sequence_id: str  # References a SequenceConfig in the registry (config.py)
    status: OrchestrationStatus = OrchestrationStatus.pending
    current_step_index: int = Field(default=0, ge=0)  # Index into SequenceConfig.steps[]
    enrolled_at: datetime  # Anchor for day_offset timing calculations
    scheduled_next_at: datetime | None = None  # When the scheduler should next process this state
    engagement_score: int = Field(default=0, ge=0)  # Recomputed on each event by scorer.py
    stop_reason: str | None = None  # Set when entering a stopped_* terminal state
    paused_reason: str | None = None  # Set when auto-paused (failure ladder) or manually paused
    completed_steps: list[str] = Field(default_factory=list)  # step_ids that executed successfully
    # P2-2 / deep audit HIGH-4: retry ladder counter. Incremented on each consecutive
    # failure, reset to 0 on any success. When it reaches MAX_CONSECUTIVE_FAILURES
    # the sequence is paused. See dispatcher._handle_failure_with_ladder().
    consecutive_failure_count: int = Field(default=0, ge=0)
    # Optimistic locking version — maps to ORM version_id_col. SQLAlchemy auto-increments
    # this on every UPDATE and adds WHERE version = :old, preventing silent overwrites
    # when two writers (scheduler + webhook ingestion) race on the same row.
    version: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class EngagementEvent(BaseModel):
    """
    A normalised engagement or system event linked to a specific lead.

    Events are immutable once written.  The occurred_at timestamp is taken
    from the upstream provider payload (not the ingestion time) to ensure
    correct ordering even when webhooks arrive out of order.
    """

    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(default_factory=_new_uuid)
    lead_id: str
    event_type: EventType
    occurred_at: datetime
    ingested_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Executions
# ---------------------------------------------------------------------------


class ActionExecution(BaseModel):
    """
    Record of a single attempt to execute a sequence step for a lead.

    The execution_id is also the idempotency key.  Its format is
    ``{state_id}_{step_id}_{attempt}`` so the dispatcher can check existence
    before re-executing on retry.
    """

    model_config = ConfigDict(populate_by_name=True)

    execution_id: str = Field(
        default_factory=_new_uuid,
        description="Format: {state_id}_{step_id}_{attempt}",
    )
    lead_id: str
    step_id: str
    action_type: StepType
    status: StepResultStatus
    attempted_at: datetime = Field(default_factory=_utcnow)
    result_payload: dict[str, Any] | None = None
    error_message: str | None = None
    state_id: str


# ---------------------------------------------------------------------------
# Manual tasks
# ---------------------------------------------------------------------------


class ManualTask(BaseModel):
    """
    A human-action item surfaced to the sales rep by the orchestration layer.
    """

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(default_factory=_new_uuid)
    lead_id: str
    account_id: str | None = None
    reason: str
    priority: TaskPriority
    suggested_action: str
    due_date: datetime
    status: TaskStatus = TaskStatus.open
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditLog(BaseModel):
    """
    An immutable audit entry written on every state transition or significant
    orchestration action.
    """

    model_config = ConfigDict(populate_by_name=True)

    log_id: str = Field(default_factory=_new_uuid)
    lead_id: str | None = None
    state_id: str | None = None
    entity_type: str
    entity_id: str
    action: str
    from_status: str | None = None
    to_status: str | None = None
    trigger: str | None = None
    detail: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Step results & API request/response models
# ---------------------------------------------------------------------------


class StepResult(BaseModel):
    """Result returned by a step handler after execution."""

    model_config = ConfigDict(populate_by_name=True)

    step_id: str
    status: StepResultStatus
    execution_id: str | None = None
    detail: str | None = None


class EnrollRequest(BaseModel):
    """Request body for POST /leads/{lead_id}/enroll."""

    model_config = ConfigDict(populate_by_name=True)

    sequence_id: str
    override_start_date: datetime | None = None


class PauseRequest(BaseModel):
    """Request body for POST /leads/{lead_id}/pause."""

    model_config = ConfigDict(populate_by_name=True)

    reason: str


class IngestEventResponse(BaseModel):
    """Response returned from POST /events/ingest."""

    model_config = ConfigDict(populate_by_name=True)

    event_id: str
    lead_id: str
    actions_triggered: list[str] = Field(default_factory=list)


class SchedulerRunSummary(BaseModel):
    """Summary returned from POST /scheduler/run."""

    model_config = ConfigDict(populate_by_name=True)

    states_processed: int = 0
    steps_executed: int = 0
    steps_skipped: int = 0
    steps_failed: int = 0
    steps_deferred: int = 0  # Fix 22
    errors: list[str] = Field(default_factory=list)
