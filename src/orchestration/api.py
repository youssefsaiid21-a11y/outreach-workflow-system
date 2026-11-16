"""
FastAPI router for the orchestration module.

All endpoints are mounted under /orchestration.  The router is included by
src/api/router.py with the global API key authentication dependency.

Endpoint summary:
    POST /leads/{lead_id}/enroll        -  Enrol a lead in a sequence
    POST /events/ingest                 -  Ingest a provider webhook event
    GET  /scheduler/due                 -  List due sequence states
    POST /scheduler/run                 -  Execute one full scheduler cycle
    POST /leads/{lead_id}/pause         -  Pause an active sequence
    POST /leads/{lead_id}/resume        -  Resume a paused sequence
    GET  /leads/{lead_id}/timeline      -  Full activity timeline for a lead
    GET  /tasks                         -  List open manual tasks
    POST /tasks/{task_id}/status        -  Update a task's status
    GET  /linkedin/queue-health         -  Detailed LinkedIn queue health + stuck detection
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..api.auth import require_api_key, get_current_user
from ..db.database import get_db
from .config import get_sequence_config
from .enums import OrchestrationStatus, TaskStatus
from .ingestion import ingest_event
from .models import (
    EnrollRequest,
    IngestEventResponse,
    LeadContext,
    LeadSequenceState,
    ManualTask,
    PauseRequest,
    SchedulerRunSummary,
)
from .providers import PhantomBusterLinkedInProvider, StubLinkedInProvider
from .repositories import (
    ActionExecutionRepository,
    AuditLogRepository,
    EngagementEventRepository,
    LeadSequenceStateRepository,
    ManualTaskRepository,
    ProspectRepository,
    _orm_to_state,
)
from .state_machine import is_terminal, transition_state

logger = logging.getLogger(__name__)


def _assert_lead_ownership(db: Session, lead_id: str, user_id: str) -> None:
    """Raise 403 if the lead does not belong to the user."""
    from ..db.models import Prospect
    p = db.query(Prospect.user_id).filter_by(id=lead_id).first()
    if p and p[0] and p[0] != user_id:
        raise HTTPException(status_code=403, detail="Lead does not belong to you.")


def _internal_error(exc: Exception, label: str) -> HTTPException:
    """Build a 500 HTTPException that never echoes the raw exception string
    to the client. The full traceback is logged server-side with ``label`` so
    operators can find the call site in the JSON log stream.

    Consolidates the pattern used by enroll_lead (FIX-20), ingest_event, and
    the 4 sites flagged in the deep audit CRITICAL-6."""
    logger.error("%s: %s", label, exc, exc_info=True)
    return HTTPException(status_code=500, detail="Internal error. See server logs.")

# Load any custom sequence config saved in the DB so it is available as soon
# as the orchestration module is imported (i.e. at app startup).
try:
    from .config import load_custom_config_from_db
    load_custom_config_from_db()
except Exception as _exc:
    logger.warning("Could not load custom sequence config at startup: %s", _exc)

router = APIRouter(prefix="/orchestration", tags=["orchestration"])

_API_AUTH = [Depends(require_api_key)]


# Fix 15: Typed request model for task status update
class TaskStatusUpdateRequest(BaseModel):
    """Request body for POST /tasks/{task_id}/status."""
    status: TaskStatus


class LinkedInActionRequest(BaseModel):
    """Request body for POST /leads/{lead_id}/linkedin/action."""
    action_type: str  # connect_sent | connected | message_sent | message_replied
    note: str | None = None


# ── Per-user provider registry (P3-2) ──────────────────────────────────────
# Phase 3 fix P3-2 / deep audit CRITICAL-3 + HIGH-7: the old module-level
# `_linkedin_provider` global meant every rep's leads were messaged via the
# FIRST rep's PhantomBuster session. This cache is now keyed by user_id
# so each rep gets an isolated provider instance backed by their own
# app_settings entries.
#
# Cache entries live for the lifetime of the process unless explicitly
# invalidated by `/admin/refresh-providers?user_id=<uid>`. Call without a
# user_id to clear the whole cache after a mass key rotation.

# _PROVIDER_CACHE_MAX bounds the number of distinct user entries we hold
# at once. Set to a generous upper bound on realistic concurrent reps.
_PROVIDER_CACHE_MAX = 64

_providers_cache: dict[str | None, tuple] = {}


def _reset_providers(user_id: str | None = None) -> None:
    """Drop cached provider instances so fresh credentials are picked up.

    Args:
        user_id: if provided, only that user's entry is evicted. If None,
                 the entire cache is cleared (useful after a mass env-var
                 rotation that affects the legacy admin fallback).
    """
    if user_id is None:
        n = len(_providers_cache)
        _providers_cache.clear()
        logger.info("Provider cache cleared (%d entr%s)", n, "y" if n == 1 else "ies")
        return
    if user_id in _providers_cache:
        del _providers_cache[user_id]
        logger.info("Provider cache evicted for user_id=%s", user_id)


def _lookup_user_setting(db: Session | None, user_id: str, key: str) -> str | None:
    """Read a user-scoped app_settings value via the ``__user__{uid}__{key}``
    namespaced key convention (audit-#7 FIX-17, FIX-43). Returns None if
    missing or empty. Opens a short-lived session if none is passed so the
    cache can also be primed from the background scheduler thread.
    """
    from ..db.database import SessionLocal
    from ..db.models import AppSetting

    owned_session = False
    if db is None:
        db = SessionLocal()
        owned_session = True
    try:
        row = (
            db.query(AppSetting)
            .filter_by(key=f"__user__{user_id}__{key}")
            .first()
        )
        val = (row.value or "").strip() if row else ""
        return val or None
    finally:
        if owned_session:
            db.close()


def _build_providers_for_user(
    user_id: str | None,
    db: Session | None,
) -> PhantomBusterLinkedInProvider | StubLinkedInProvider:
    """Construct a LinkedIn provider for ``user_id``.

    Credential lookup order, highest priority first:

      1. Per-user app_settings row keyed ``__user__{uid}__{KEY}``
      2. Process env var (``PHANTOMBUSTER_API_KEY``, etc.)  -  legacy fallback.

    Degrades to ``StubLinkedInProvider`` if no PhantomBuster key is present.
    """
    def _pick(key: str) -> str:
        if user_id:
            val = _lookup_user_setting(db, user_id, key)
            if val:
                return val
        return (os.getenv(key, "") or "").strip()

    pb_key = _pick("PHANTOMBUSTER_API_KEY")
    pb_connect = _pick("PHANTOMBUSTER_CONNECT_AGENT_ID")
    pb_message = _pick("PHANTOMBUSTER_MESSAGE_AGENT_ID")

    if pb_key:
        linkedin_provider: PhantomBusterLinkedInProvider | StubLinkedInProvider = (
            PhantomBusterLinkedInProvider(
                api_key=pb_key,
                connect_agent_id=pb_connect or None,
                message_agent_id=pb_message or None,
            )
        )
        logger.info(
            "Providers(user=%s): PhantomBuster (LinkedIn)",
            user_id or "<legacy>",
        )
    else:
        linkedin_provider = StubLinkedInProvider()
        logger.info(
            "Providers(user=%s): LinkedIn: Stub  -  no PhantomBuster key",
            user_id or "<legacy>",
        )
    return linkedin_provider


def _get_providers(
    user_id: str | None = None,
    db: Session | None = None,
) -> PhantomBusterLinkedInProvider | StubLinkedInProvider:
    """Return the LinkedIn provider for ``user_id``.

    Lazily constructs the provider on first access and caches it until the
    process exits or an operator calls ``/admin/refresh-providers``. A
    None ``user_id`` is the "legacy" cache slot used by unauthenticated
    code paths (tests, the admin CLI, first-run installs)  -  it shares the
    same env-var fallback as everyone else.

    Evicts the oldest entry if the cache reaches ``_PROVIDER_CACHE_MAX``.
    """
    if user_id in _providers_cache:
        return _providers_cache[user_id]

    pair = _build_providers_for_user(user_id, db)

    if len(_providers_cache) >= _PROVIDER_CACHE_MAX:
        # FIFO eviction  -  simple and deterministic. We expect at most a
        # handful of reps per process, so this branch is almost never hit.
        try:
            oldest = next(iter(_providers_cache))
            del _providers_cache[oldest]
            logger.info("Provider cache evicted oldest entry user_id=%s (at capacity)", oldest)
        except StopIteration:
            pass
    _providers_cache[user_id] = pair
    return pair


# ---------------------------------------------------------------------------
# Dependency: default dispatcher
# ---------------------------------------------------------------------------


def _get_dispatcher(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    FastAPI dependency that builds an ActionDispatcher scoped to the
    authenticated user.

    P3-2: providers are cached per-user, so the dispatcher looks up
    the caller's PhantomBuster credentials and builds the dispatcher
    against them. Callers that don't pass auth (tests, first-run installs)
    fall through to the None cache slot which still reads env vars,
    preserving the legacy single-tenant behaviour.
    """
    from .dispatcher import ActionDispatcher

    user_id = getattr(current_user, "id", None) if current_user is not None else None
    linkedin_provider = _get_providers(user_id=user_id, db=db)
    return ActionDispatcher(
        linkedin_provider=linkedin_provider,
        db=db,
        config_registry=get_sequence_config,
    )


# ---------------------------------------------------------------------------
# Helper: ORM row → Pydantic dict
# ---------------------------------------------------------------------------


def _state_to_dict(row: Any) -> dict:
    """Convert an OrchSequenceStateORM row to a JSON-serialisable dict."""
    return {
        "state_id": row.state_id,
        "lead_id": row.lead_id,
        "sequence_id": row.sequence_id,
        "status": row.status,
        "current_step_index": row.current_step_index,
        "enrolled_at": row.enrolled_at.isoformat() if row.enrolled_at else None,
        "scheduled_next_at": row.scheduled_next_at.isoformat() if row.scheduled_next_at else None,
        "engagement_score": row.engagement_score,
        "stop_reason": row.stop_reason,
        "paused_reason": row.paused_reason,
        "completed_steps": row.completed_steps or [],
    }


def _pydantic_state_to_dict(state: LeadSequenceState) -> dict:
    """Convert a LeadSequenceState Pydantic model to a JSON-serialisable dict."""
    return {
        "state_id": state.state_id,
        "lead_id": state.lead_id,
        "sequence_id": state.sequence_id,
        "status": state.status.value,
        "current_step_index": state.current_step_index,
        "enrolled_at": state.enrolled_at.isoformat() if state.enrolled_at else None,
        "scheduled_next_at": state.scheduled_next_at.isoformat() if state.scheduled_next_at else None,
        "engagement_score": state.engagement_score,
        "stop_reason": state.stop_reason,
        "paused_reason": state.paused_reason,
        "completed_steps": state.completed_steps or [],
    }


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------


@router.post("/leads/{lead_id}/enroll", status_code=201, dependencies=_API_AUTH)
def enroll_lead(
    lead_id: str,
    body: EnrollRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Enrol a lead in an outreach sequence.

    Validates:
    - The lead exists in the prospects table.
    - The sequence_id is registered.
    - No active (non-terminal) sequence already running for this lead (409 if so).

    Creates a LeadSequenceState with status=pending, then immediately
    transitions to active.

    Args:
        lead_id: Prospect.id from the URL path.
        body:    EnrollRequest with sequence_id and optional start date.
        db:      Injected DB session.

    Returns:
        LeadSequenceState as a dict.
    """
    _assert_lead_ownership(db, lead_id, current_user.id)
    prospect_repo = ProspectRepository(db)
    prospect = prospect_repo.get(lead_id)
    if prospect is None:
        raise HTTPException(status_code=404, detail=f"Lead '{lead_id}' not found")

    # Validate sequence exists
    try:
        config = get_sequence_config(body.sequence_id)
    except KeyError as exc:
        logger.warning("enroll_lead: sequence not found: %s", exc)
        raise HTTPException(status_code=404, detail="Resource not found.")

    # Edge case 3: reject if already active
    state_repo = LeadSequenceStateRepository(db)
    existing = state_repo.get_active_for_lead(lead_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Lead '{lead_id}' already has an active sequence "
                f"(state_id={existing.state_id}, status={existing.status})."
            ),
        )

    audit_repo = AuditLogRepository(db)

    enrolled_at = body.override_start_date or datetime.now(timezone.utc)

    # Calculate first step's scheduled_next_at
    first_scheduled = None
    if config.steps:
        first_step = config.steps[0]
        from datetime import timedelta
        first_scheduled = enrolled_at + timedelta(days=first_step.day_offset)

    state = LeadSequenceState(
        lead_id=lead_id,
        sequence_id=body.sequence_id,
        status=OrchestrationStatus.pending,
        enrolled_at=enrolled_at,
        scheduled_next_at=first_scheduled,
    )

    # Transition pending → active
    state = transition_state(
        state=state,
        trigger="enrolled",
        event=None,
        audit_repo=audit_repo,
    )

    state_repo.upsert_from_pydantic(state)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise _internal_error(exc, f"enroll_lead commit failed for lead={lead_id}")

    return _pydantic_state_to_dict(state)


# ---------------------------------------------------------------------------
# Event ingestion
# ---------------------------------------------------------------------------


@router.post("/events/ingest")  # No auth  -  external providers call this
def ingest_event_endpoint(
    payload: dict,
    provider: str = Query(default="phantombuster", description="Provider name (e.g. 'phantombuster')"),
    secret: str = Query(default="", description="Webhook secret as URL query (for providers that don't support custom headers)"),
    x_webhook_secret: str = Header(default="", alias="X-Webhook-Secret"),
    db: Session = Depends(get_db),
) -> IngestEventResponse:
    """
    Ingest a raw webhook event payload from PhantomBuster or manual UI actions.

    The payload shape is provider-specific. The ingestion layer normalises
    it to an internal EngagementEvent.

    No API key required  -  PhantomBuster external providers can't send one.

    Authentication
    --------------
    The configured `WEBHOOK_SECRET` env var must match either:
      - the `X-Webhook-Secret` HTTP header (preferred), OR
      - the `?secret=...` URL query parameter (for providers that don't support
        custom headers  -  embed the secret in the target URL).

    Both compared with `hmac.compare_digest` to defeat timing attacks.
    """
    import hmac as _hmac
    expected_secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if not expected_secret:
        raise HTTPException(
            status_code=503,
            detail="Webhook endpoint unavailable  -  WEBHOOK_SECRET is not configured.",
        )
    # Accept the secret from either the header or the query string. Prefer the
    # header if present, fall back to the query param. Both go through
    # constant-time comparison.
    presented = x_webhook_secret or secret
    if not _hmac.compare_digest(presented, expected_secret):
        raise HTTPException(status_code=403, detail="Invalid webhook secret.")
    try:
        return ingest_event(payload=payload, db=db, provider=provider)
    except Exception as exc:
        raise _internal_error(exc, "ingest_event_endpoint failed")


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


@router.get("/scheduler/due", dependencies=_API_AUTH)
def get_due_states(db: Session = Depends(get_db)) -> list[dict]:
    """
    Return all sequence states where the next step is currently due.

    Useful for monitoring and debugging the scheduler without executing steps.

    Args:
        db: Injected DB session.

    Returns:
        List of state dicts.
    """
    from .scheduler import get_due_steps

    now = datetime.now(timezone.utc)
    states = get_due_steps(now, db)
    return [_pydantic_state_to_dict(s) for s in states]


@router.post("/scheduler/run", dependencies=_API_AUTH)
def run_scheduler_endpoint(
    db: Session = Depends(get_db),
    dispatcher=Depends(_get_dispatcher),
) -> SchedulerRunSummary:
    """
    Execute one full scheduler cycle: find all due states and run their next steps.

    This endpoint is idempotent  -  running it twice in quick succession will
    skip already-executed steps.

    Args:
        db:         Injected DB session.
        dispatcher: Injected ActionDispatcher.

    Returns:
        SchedulerRunSummary with counts of outcomes.
    """
    from .scheduler import run_scheduler

    now = datetime.now(timezone.utc)
    return run_scheduler(now=now, db=db, dispatcher=dispatcher)


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------


@router.post("/leads/{lead_id}/pause", dependencies=_API_AUTH)
def pause_lead_sequence(
    lead_id: str,
    body: PauseRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Pause the active sequence for a lead.

    Args:
        lead_id: Prospect.id from the URL path.
        body:    PauseRequest with a reason string.
        db:      Injected DB session.

    Returns:
        Updated LeadSequenceState as a dict.

    Raises:
        404 if no active sequence found.
        409 if the sequence is already in a terminal state.
    """
    _assert_lead_ownership(db, lead_id, current_user.id)
    state_repo = LeadSequenceStateRepository(db)
    audit_repo = AuditLogRepository(db)

    state_orm = state_repo.get_active_for_lead(lead_id)
    if state_orm is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active sequence found for lead '{lead_id}'",
        )

    state = LeadSequenceState(
        state_id=state_orm.state_id,
        lead_id=state_orm.lead_id,
        sequence_id=state_orm.sequence_id,
        status=OrchestrationStatus(state_orm.status),
        current_step_index=state_orm.current_step_index,
        enrolled_at=state_orm.enrolled_at,
        scheduled_next_at=state_orm.scheduled_next_at,
        engagement_score=state_orm.engagement_score,
        stop_reason=state_orm.stop_reason,
        paused_reason=state_orm.paused_reason,
        completed_steps=state_orm.completed_steps or [],
    )

    if is_terminal(state.status):
        raise HTTPException(
            status_code=409,
            detail=f"Sequence is already in terminal status '{state.status.value}'",
        )

    try:
        state = transition_state(
            state=state,
            trigger="pause",
            event=None,
            audit_repo=audit_repo,
        )
    except ValueError as exc:
        logger.warning("pause_lead: transition conflict: %s", exc)
        raise HTTPException(status_code=409, detail="Conflict. The resource was modified concurrently.")

    state.paused_reason = body.reason
    state_repo.upsert_from_pydantic(state)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise _internal_error(exc, f"pause_lead_sequence commit failed for lead={lead_id}")

    return _pydantic_state_to_dict(state)


@router.post("/leads/{lead_id}/resume", dependencies=_API_AUTH)
def resume_lead_sequence(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Resume a paused sequence for a lead.

    Args:
        lead_id: Prospect.id from the URL path.
        db:      Injected DB session.

    Returns:
        Updated LeadSequenceState as a dict.

    Raises:
        404 if no paused sequence found.
        409 if the sequence is not in the paused state.
    """
    _assert_lead_ownership(db, lead_id, current_user.id)
    state_repo = LeadSequenceStateRepository(db)
    audit_repo = AuditLogRepository(db)

    # Look for any non-terminal state (active/waiting/paused)
    state_orm = state_repo.get_active_for_lead(lead_id)
    if state_orm is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active/paused sequence found for lead '{lead_id}'",
        )

    if state_orm.status != OrchestrationStatus.paused.value:
        raise HTTPException(
            status_code=409,
            detail=f"Sequence is in status '{state_orm.status}', not 'paused'",
        )

    state = LeadSequenceState(
        state_id=state_orm.state_id,
        lead_id=state_orm.lead_id,
        sequence_id=state_orm.sequence_id,
        status=OrchestrationStatus(state_orm.status),
        current_step_index=state_orm.current_step_index,
        enrolled_at=state_orm.enrolled_at,
        scheduled_next_at=state_orm.scheduled_next_at,
        engagement_score=state_orm.engagement_score,
        stop_reason=state_orm.stop_reason,
        paused_reason=state_orm.paused_reason,
        completed_steps=state_orm.completed_steps or [],
    )

    try:
        state = transition_state(
            state=state,
            trigger="resume",
            event=None,
            audit_repo=audit_repo,
        )
    except ValueError as exc:
        logger.warning("resume_lead: transition conflict: %s", exc)
        raise HTTPException(status_code=409, detail="Conflict. The resource was modified concurrently.")

    state.paused_reason = None
    state_repo.upsert_from_pydantic(state)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise _internal_error(exc, f"resume_lead_sequence commit failed for lead={lead_id}")

    return _pydantic_state_to_dict(state)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


@router.get("/leads/{lead_id}/timeline", dependencies=_API_AUTH)
def get_lead_timeline(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    """
    Return an ordered timeline of all actions and events for a lead.

    Each item has a 'type' field set to "action" or "event" for frontend
    consumption.  Items are sorted by their timestamp ascending.

    Args:
        lead_id: Prospect.id from the URL path.
        db:      Injected DB session.

    Returns:
        List of timeline item dicts sorted by timestamp.
    """
    action_repo = ActionExecutionRepository(db)
    _assert_lead_ownership(db, lead_id, current_user.id)
    event_repo = EngagementEventRepository(db)

    actions = action_repo.get_by_lead(lead_id)
    events = event_repo.get_by_lead(lead_id)

    timeline: list[dict] = []

    for a in actions:
        timeline.append({
            "type": "action",
            "timestamp": a.attempted_at.isoformat() if a.attempted_at else None,
            "execution_id": a.execution_id,
            "step_id": a.step_id,
            "action_type": a.action_type,
            "status": a.status,
            "error_message": a.error_message,
            "result_payload": a.result_payload,
        })

    for e in events:
        timeline.append({
            "type": "event",
            "timestamp": e.occurred_at.isoformat() if e.occurred_at else None,
            "event_id": e.event_id,
            "event_type": e.event_type,
            "ingested_at": e.ingested_at.isoformat() if e.ingested_at else None,
            "metadata": e.event_metadata,
        })

    # Sort by timestamp, nulls last
    timeline.sort(
        key=lambda x: x.get("timestamp") or "9999",
    )

    return timeline


# ---------------------------------------------------------------------------
# Manual tasks
# ---------------------------------------------------------------------------


@router.get("/tasks", dependencies=_API_AUTH)
def list_tasks(
    priority: str | None = Query(default=None, description="Filter by priority (urgent/high/medium/low)"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    """
    Return all open manual tasks, optionally filtered by priority.

    Tasks are sorted by priority descending (urgent first), then created_at ascending.

    Args:
        priority: Optional priority string filter.
        db:       Injected DB session.

    Returns:
        List of task dicts.
    """
    _PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}

    task_repo = ManualTaskRepository(db)
    tasks = task_repo.get_open_tasks(priority=priority)

    # Filter to only this user's leads
    from ..db.models import Prospect
    user_lead_ids = {p.id for p in db.query(Prospect.id).filter_by(user_id=current_user.id).all()}
    tasks = [t for t in tasks if t.lead_id in user_lead_ids]

    result = []
    for t in tasks:
        result.append({
            "task_id": t.task_id,
            "lead_id": t.lead_id,
            "account_id": t.account_id,
            "reason": t.reason,
            "priority": t.priority,
            "suggested_action": t.suggested_action,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })

    result.sort(
        key=lambda x: (_PRIORITY_ORDER.get(x["priority"], 99), x.get("created_at") or "")
    )
    return result


@router.post("/tasks/{task_id}/status", dependencies=_API_AUTH)
def update_task_status(
    task_id: str,
    body: TaskStatusUpdateRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    Update the status of a manual task.

    Args:
        task_id: UUID of the ManualTaskORM row.
        body:    TaskStatusUpdateRequest with status field (Fix 15).
        db:      Injected DB session.

    Returns:
        Updated task dict.

    Raises:
        404 if task not found.
    """
    new_status = body.status

    task_repo = ManualTaskRepository(db)
    updated = task_repo.update_status(task_id, new_status)

    if updated is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise _internal_error(exc, f"update_task_status commit failed for task={task_id}")

    return {
        "task_id": updated.task_id,
        "lead_id": updated.lead_id,
        "reason": updated.reason,
        "priority": updated.priority,
        "status": updated.status,
        "updated_at": updated.updated_at.isoformat() if updated.updated_at else None,
    }


# ---------------------------------------------------------------------------
# All states (for Sequences UI)
# ---------------------------------------------------------------------------


@router.get("/states", dependencies=_API_AUTH)
def list_states(
    include_terminal: bool = Query(default=False, description="Include completed/stopped states"),
    campaign_id: str | None = Query(
        default=None,
        description=(
            "Scope the SendLog-derived email_sent_count to this campaign only. "
            "When omitted, the count falls back to the lead's most recent "
            "EmailSequence (global Sequences view). Pass this when you're "
            "rendering a specific campaign's monitor so the count is always "
            "the right one for that campaign."
        ),
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    """
    Return all sequence states with enriched lead info for the Sequences UI.

    Args:
        include_terminal: Set true to include completed/stopped states.
        campaign_id:      Optional campaign scope for ``email_sent_count``
                           -  see the parameter docstring.
        db:               Injected DB session.

    Returns:
        List of state dicts enriched with lead name, email, company, linkedin info.
    """
    # Filter to only this user's leads at the DB level
    from ..db.models import Prospect, SendLog, EmailSequence
    from ..orchestration.orm import OrchSequenceStateORM, EngagementEventORM
    from ..orchestration.repositories import _ACTIVE_STATUSES
    from sqlalchemy import func as _f

    user_prospect_ids = {p.id for p in db.query(Prospect.id).filter_by(user_id=current_user.id).all()}

    q = db.query(OrchSequenceStateORM).filter(
        OrchSequenceStateORM.lead_id.in_(user_prospect_ids)
    )
    if not include_terminal:
        q = q.filter(OrchSequenceStateORM.status.in_(_ACTIVE_STATUSES))
    rows = q.order_by(OrchSequenceStateORM.enrolled_at.desc()).all()

    lead_ids = list({r.lead_id for r in rows})
    prospects = {p.id: p for p in db.query(Prospect).filter(Prospect.id.in_(lead_ids)).all()} if lead_ids else {}

    # Ground truth for sent emails lives in the SendLog table.
    # We join it here so the frontend can label each step correctly.
    #
    # Scoping strategy (2026-04-11 follow-up to make it robust for every
    # campaign, not just Nasser v1.1):
    #   1. If the caller passed ``campaign_id`` explicitly, scope to that
    #      campaign. This is what the per-campaign monitor view does  -  it
    #      always knows which campaign is on screen and can tell us
    #      unambiguously. Zero heuristics, zero cross-campaign bleed.
    #   2. Otherwise, fall back to "the lead's most recent EmailSequence"
    #       -  good enough for the global Sequences tab where the view is
    #      cross-campaign anyway.
    sent_step_map: dict[str, set[int]] = {lid: set() for lid in lead_ids}
    sent_date_map: dict[str, dict[int, str]] = {}  # pid → {step: iso_date}
    if lead_ids:
        if campaign_id:
            # Explicit scope  -  single query for the whole batch.
            rows_sl = (
                db.query(SendLog.prospect_id, SendLog.step, _f.count())
                .filter(
                    SendLog.prospect_id.in_(lead_ids),
                    SendLog.campaign_id == campaign_id,
                    SendLog.status == "sent",
                )
                .group_by(SendLog.prospect_id, SendLog.step)
                .all()
            )
            for pid, step, _n in rows_sl:
                if step:
                    sent_step_map.setdefault(pid, set()).add(int(step))
            # Also fetch sent_at dates for the sent steps (campaign-scoped)
            rows_sl_dates = (
                db.query(SendLog.prospect_id, SendLog.step, SendLog.sent_at)
                .filter(
                    SendLog.prospect_id.in_(lead_ids),
                    SendLog.campaign_id == campaign_id,
                    SendLog.status == "sent",
                    SendLog.sent_at.isnot(None),
                )
                .all()
            )
            for pid, step, sent_at in rows_sl_dates:
                if step and sent_at:
                    sent_date_map.setdefault(pid, {})[int(step)] = sent_at.isoformat()
        else:
            # Fallback: resolve each lead's most-recent EmailSequence →
            # gives us the campaign_id for scoping the SendLog count.
            active_es = (
                db.query(EmailSequence.prospect_id, EmailSequence.campaign_id)
                .filter(EmailSequence.prospect_id.in_(lead_ids))
                .order_by(
                    EmailSequence.prospect_id,
                    EmailSequence.created_at.desc().nullslast(),
                )
                .all()
            )
            lead_campaign: dict[str, str] = {}
            for pid, cid in active_es:
                if pid not in lead_campaign and cid:
                    lead_campaign[pid] = cid
            # Group by campaign so we can batch the SendLog scans.
            by_campaign: dict[str, list[str]] = {}
            for pid, cid in lead_campaign.items():
                by_campaign.setdefault(cid, []).append(pid)
            for cid, pids in by_campaign.items():
                rows_sl = (
                    db.query(SendLog.prospect_id, SendLog.step, _f.count())
                    .filter(
                        SendLog.prospect_id.in_(pids),
                        SendLog.campaign_id == cid,
                        SendLog.status == "sent",
                    )
                    .group_by(SendLog.prospect_id, SendLog.step)
                    .all()
                )
                for pid, step, _n in rows_sl:
                    if step:
                        sent_step_map.setdefault(pid, set()).add(int(step))

                # Also fetch sent_at dates for the sent steps (for timeline labels)
                rows_sl_dates = (
                    db.query(SendLog.prospect_id, SendLog.step, SendLog.sent_at)
                    .filter(
                        SendLog.prospect_id.in_(pids),
                        SendLog.campaign_id == cid,
                        SendLog.status == "sent",
                        SendLog.sent_at.isnot(None),
                    )
                    .all()
                )
                for pid, step, sent_at in rows_sl_dates:
                    if step and sent_at:
                        sent_date_map.setdefault(pid, {})[int(step)] = sent_at.isoformat()

    # Batch-fetch LinkedIn message delivery confirmations from linkedin_queue.
    # Same pattern as the SendLog batch above: single query, O(1) lookup per lead.
    # FIX-2: scope by campaign_id when provided (matches SendLog pattern).
    # FIX-3: .distinct() avoids duplicate rows for multi-attempt prospects.
    li_msg_confirmed: set[str] = set()  # prospect_ids with confirmed message delivery
    if lead_ids:
        from ..db.models import LinkedInQueue, LinkedInQueueStatus
        li_q = (
            db.query(LinkedInQueue.prospect_id)
            .filter(
                LinkedInQueue.prospect_id.in_(lead_ids),
                LinkedInQueue.action_type == "message",
                LinkedInQueue.status == LinkedInQueueStatus.done,
            )
        )
        if campaign_id:
            # Include entries that match this campaign OR have NULL campaign_id
            # (legacy entries predate campaign_id tracking but still belong here).
            from sqlalchemy import or_
            li_q = li_q.filter(or_(
                LinkedInQueue.campaign_id == campaign_id,
                LinkedInQueue.campaign_id.is_(None),
            ))
        li_done_rows = li_q.distinct().all()
        li_msg_confirmed = {r[0] for r in li_done_rows}

        # FIX-1: also check EngagementEvents for manual "Mark Message Sent"
        # actions, which bypass linkedin_queue entirely.
        li_manual_rows = (
            db.query(EngagementEventORM.lead_id)
            .filter(
                EngagementEventORM.lead_id.in_(lead_ids),
                EngagementEventORM.event_type == "linkedin_message_sent",
            )
            .distinct()
            .all()
        )
        li_msg_confirmed |= {r[0] for r in li_manual_rows}

    # Build a step_id → step_type mapping for all sequences referenced by
    # these orch states so we can resolve completed_steps into actionable
    # flags (linkedin_connect_sent, linkedin_message_sent) for the frontend.
    seq_ids = {row.sequence_id for row in rows}
    step_type_map: dict[str, str] = {}  # step_id → step_type string
    for sid in seq_ids:
        try:
            cfg = get_sequence_config(sid)
            for s in cfg.steps:
                step_type_map[s.step_id] = s.step_type.value if hasattr(s.step_type, "value") else str(s.step_type)
        except (KeyError, Exception):
            pass  # unknown sequence; completed_steps won't resolve

    result = []
    for row in rows:
        p = prospects.get(row.lead_id)
        d = _state_to_dict(row)
        d["lead_name"] = f"{p.first_name} {p.last_name or ''}".strip() if p else "Unknown"
        d["lead_email"] = p.email if p else ""
        d["lead_company"] = (p.company or "") if p else ""
        d["lead_linkedin_url"] = getattr(p, "linkedin_url", None) if p else None
        d["linkedin_connected"] = bool(getattr(p, "linkedin_connected", False)) if p else False
        # Ground-truth list of email step numbers (1-based) confirmed sent.
        sent_steps = sorted(sent_step_map.get(row.lead_id, set()))
        d["email_sent_steps"] = sent_steps
        d["email_sent_count"] = len(sent_steps)
        d["email_sent_dates"] = sent_date_map.get(row.lead_id, {})
        # LinkedIn ground truth: use Prospect timestamps set ONLY when
        # PhantomBuster confirms actual delivery, not when we queued.
        # linkedin_connect_sent_at → PB returned "invitationSent"
        # linkedin_connected      → connection accepted
        d["linkedin_connect_sent"] = bool(getattr(p, "linkedin_connect_sent_at", None)) if p else False
        d["linkedin_message_sent"] = row.lead_id in li_msg_confirmed
        # Also flag if the step was queued (orch dispatched) but not yet confirmed
        completed_types = {step_type_map.get(sid, "") for sid in (row.completed_steps or [])}
        d["linkedin_connect_queued"] = "send_linkedin_connect" in completed_types
        d["linkedin_message_queued"] = "send_linkedin_message" in completed_types
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# LinkedIn manual actions
# ---------------------------------------------------------------------------


@router.post("/leads/{lead_id}/linkedin/action", dependencies=_API_AUTH)
def record_linkedin_action(
    lead_id: str,
    body: LinkedInActionRequest,
    db: Session = Depends(get_db),
) -> IngestEventResponse:
    """
    Manually record a LinkedIn action performed by the sales rep.

    This is the bridge between the sales rep's manual LinkedIn work and the
    orchestration engine.  When the rep sends a connection request, marks a
    lead as connected, or sends a message, they record it here so the state
    machine and scoring engine stay in sync.

    Args:
        lead_id:  Prospect.id from the URL path.
        body:     LinkedInActionRequest with action_type and optional note.
        db:       Injected DB session.

    Returns:
        IngestEventResponse with the recorded event_id and actions triggered.
    """
    action_map = {
        "connect_sent":    "linkedin_connect_sent",
        "connected":       "linkedin_connect_accepted",
        "message_sent":    "linkedin_message_sent",
        "message_replied": "linkedin_message_replied",
    }
    event_type_str = action_map.get(body.action_type)
    if event_type_str is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action_type '{body.action_type}'. "
                   f"Valid: {list(action_map.keys())}",
        )

    payload = {
        "event_type": event_type_str,
        "lead_id": lead_id,
        "source": "manual_rep_action",
    }
    if body.note:
        payload["note"] = body.note

    try:
        return ingest_event(payload=payload, db=db, provider="manual")
    except Exception as exc:
        raise _internal_error(exc, f"record_linkedin_action failed for lead={lead_id}")


# ---------------------------------------------------------------------------
# Lead state (Fix 17)
# ---------------------------------------------------------------------------


@router.get("/leads/{lead_id}/state", dependencies=_API_AUTH, tags=["orchestration"])
def get_lead_state(lead_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> dict:
    """
    Return the current sequence state for a lead without the full timeline.

    Args:
        lead_id: Prospect.id from the URL path.
        db:      Injected DB session.

    Returns:
        LeadSequenceState as a dict.

    Raises:
        404 if no sequence state found for this lead.
    """
    _assert_lead_ownership(db, lead_id, current_user.id)
    state_repo = LeadSequenceStateRepository(db)
    state_orm = state_repo.get_active_for_lead(lead_id)
    if not state_orm:
        states = state_repo.get_by_lead(lead_id)
        if not states:
            raise HTTPException(status_code=404, detail="No sequence state found for this lead.")
        state_orm = max(states, key=lambda s: s.enrolled_at or datetime(1, 1, 1))
    return _orm_to_state(state_orm).model_dump()


# ── Admin: refresh providers ───────────────────────────────────────────────


@router.post("/admin/refresh-providers", dependencies=_API_AUTH)
def refresh_providers(user_id: str | None = Query(None, description="Evict only this user's cache entry; omit to clear the whole cache")):
    """Clear cached LinkedIn providers so new PhantomBuster API keys are picked up.

    P3-2: with the per-user provider cache, an operator can surgically
    evict a single rep after rotating their PhantomBuster credentials
    without interrupting anyone else's in-flight sequences.
    Calling without ``user_id`` clears the entire cache (legacy behavior,
    useful after a mass env-var rotation).
    """
    _reset_providers(user_id)
    if user_id:
        return {"status": "ok", "message": f"Provider cache cleared for user_id={user_id}."}
    return {"status": "ok", "message": "Provider cache cleared. New keys will be used on next request."}


# ── LinkedIn batch endpoints ──────────────────────────────────────────────


@router.post("/linkedin/flush", dependencies=[Depends(require_api_key)])
def flush_linkedin_queue_endpoint(db: Session = Depends(get_db)):
    """
    Manually drain the LinkedIn batch queue right now.

    P2-3: queue draining is normally handled by the dedicated
    `linkedin-worker` thread on its own polling interval. This admin
    endpoint forces a synchronous drain via the same code path so an
    operator can flush the queue without waiting for the next worker tick.
    Returns the per-group counts the worker emitted.
    """
    from ..services.linkedin_worker import _drain_once, linkedin_worker_alive
    if not linkedin_worker_alive():
        # Worker thread isn't running (DISABLE_LINKEDIN_WORKER, or startup
        # error). The synchronous drain is still safe  -  it uses the same
        # session-scoping code as the worker.
        logger.warning(
            "linkedin_worker is not running  -  performing synchronous drain "
            "via admin endpoint instead. Investigate why the worker is down."
        )
    _drain_once(db)
    return {"status": "ok", "message": "Drain completed. See logs for per-group details."}


@router.get("/linkedin/health", dependencies=[Depends(require_api_key)])
def get_linkedin_health(db: Session = Depends(get_db)):
    """LinkedIn outreach health summary per campaign.

    Returns connect and message counts (done/failed/queued/processing) for
    every campaign, plus an overall status flag:
      - ok:       no failed message items in the last 72h
      - warning:  some message failures (< 50%)
      - critical: >50% message failures in the last 72h

    Also returns the timestamp of each phantom's last run so stale phantoms
    (not run in >48h despite active processing items) are visible.
    """
    from ..db.models import LinkedInQueue, LinkedInQueueStatus, Campaign
    from sqlalchemy import func as _f, case

    # Per-campaign breakdown
    rows = (
        db.query(
            LinkedInQueue.campaign_id,
            LinkedInQueue.action_type,
            LinkedInQueue.status,
            _f.count(LinkedInQueue.id).label("cnt"),
        )
        .group_by(LinkedInQueue.campaign_id, LinkedInQueue.action_type, LinkedInQueue.status)
        .all()
    )

    # Build a nested map: campaign_id → action_type → status → count
    stats: dict[str, dict] = {}
    for campaign_id, action_type, status, cnt in rows:
        cid = campaign_id or "_unassigned"
        if cid not in stats:
            stats[cid] = {
                "connect": {"queued": 0, "processing": 0, "done": 0, "failed": 0},
                "message": {"queued": 0, "processing": 0, "done": 0, "failed": 0},
            }
        status_val = status.value if status else "unknown"
        if action_type in stats[cid] and status_val in stats[cid][action_type]:
            stats[cid][action_type][status_val] += cnt

    # Count message failures in last 72h for health status
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    recent = (
        db.query(
            LinkedInQueue.campaign_id,
            LinkedInQueue.status,
            _f.count(LinkedInQueue.id).label("cnt"),
        )
        .filter(
            LinkedInQueue.action_type == "message",
            LinkedInQueue.created_at >= cutoff,
        )
        .group_by(LinkedInQueue.campaign_id, LinkedInQueue.status)
        .all()
    )
    recent_by_campaign: dict[str, dict] = {}
    for cid, status, cnt in recent:
        key = cid or "_unassigned"
        if key not in recent_by_campaign:
            recent_by_campaign[key] = {"total": 0, "failed": 0}
        recent_by_campaign[key]["total"] += cnt
        if status == LinkedInQueueStatus.failed:
            recent_by_campaign[key]["failed"] += cnt

    # Fetch campaign names
    campaign_names: dict[str, str] = {}
    for c in db.query(Campaign.id, Campaign.name).all():
        campaign_names[c.id] = c.name

    # Also check no_result failures specifically (phantom never ran)
    no_result_counts = (
        db.query(LinkedInQueue.campaign_id, _f.count(LinkedInQueue.id).label("cnt"))
        .filter(
            LinkedInQueue.action_type == "message",
            LinkedInQueue.status == LinkedInQueueStatus.failed,
            LinkedInQueue.result_detail.like("no_result_after%"),
        )
        .group_by(LinkedInQueue.campaign_id)
        .all()
    )
    no_result_map = {(r.campaign_id or "_unassigned"): r.cnt for r in no_result_counts}

    campaigns_out = []
    overall_status = "ok"
    for cid, s in stats.items():
        r = recent_by_campaign.get(cid, {"total": 0, "failed": 0})
        failure_rate = r["failed"] / r["total"] if r["total"] > 0 else 0
        if failure_rate > 0.5:
            camp_status = "critical"
            overall_status = "critical"
        elif failure_rate > 0:
            camp_status = "warning"
            if overall_status != "critical":
                overall_status = "warning"
        else:
            camp_status = "ok"

        campaigns_out.append({
            "campaign_id": cid if cid != "_unassigned" else None,
            "campaign_name": campaign_names.get(cid, "Unknown"),
            "status": camp_status,
            "connect": s["connect"],
            "message": s["message"],
            "message_no_result_failures": no_result_map.get(cid, 0),
            "message_failure_rate_72h": round(failure_rate, 2),
        })

    # Sort: critical first
    campaigns_out.sort(key=lambda x: {"critical": 0, "warning": 1, "ok": 2}[x["status"]])

    # Last phantom run times from the worker's in-memory state
    from ..services.linkedin_worker import linkedin_worker_alive
    worker_alive = linkedin_worker_alive()

    return {
        "status": overall_status,
        "worker_alive": worker_alive,
        "campaigns": campaigns_out,
    }


@router.get("/linkedin/queue-health", dependencies=[Depends(require_api_key)])
def get_linkedin_queue_health(db: Session = Depends(get_db)):
    """Detailed LinkedIn queue health with stuck-item detection and worker status.

    Separate from /linkedin/health (which is per-campaign stats). This endpoint
    focuses on operational health: are items flowing through the queue? Is the
    worker alive and cycling? Are there items stuck in processing?

    Returns:
        JSON with queue counts, stuck items, worker status, recent errors, and alerts.
    """
    from ..db.models import LinkedInQueue, LinkedInQueueStatus, Prospect
    from ..services.linkedin_worker import linkedin_worker_alive, _last_cycle_at
    from sqlalchemy import func as _f
    from datetime import timedelta

    now = datetime.now(timezone.utc)

    # ── Queue status counts ──────────────────────────────────────────────
    status_counts = (
        db.query(LinkedInQueue.status, _f.count(LinkedInQueue.id))
        .group_by(LinkedInQueue.status)
        .all()
    )
    queue = {"queued": 0, "processing": 0, "done": 0, "failed": 0}
    for status, cnt in status_counts:
        status_val = status.value if status else "unknown"
        if status_val in queue:
            queue[status_val] += cnt

    # ── Stuck items: processing for >2 hours ─────────────────────────────
    cutoff_2h = now - timedelta(hours=2)
    # Detect stuck items: processing >2h OR queued >2h (queued items should be
    # picked up within one worker cycle; if they're sitting for hours something is wrong)
    stuck_rows = (
        db.query(LinkedInQueue)
        .filter(
            LinkedInQueue.status.in_([LinkedInQueueStatus.processing, LinkedInQueueStatus.queued]),
            _f.coalesce(LinkedInQueue.processed_at, LinkedInQueue.created_at) < cutoff_2h,
        )
        .order_by(_f.coalesce(LinkedInQueue.processed_at, LinkedInQueue.created_at).asc())
        .limit(50)
        .all()
    )
    stuck_items = []
    for q in stuck_rows:
        ts = q.processed_at or q.created_at
        if ts and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        hours_stuck = round((now - ts).total_seconds() / 3600, 1) if ts else 0
        # Resolve prospect name for display
        prospect_name = "Unknown"
        p = db.query(Prospect.first_name, Prospect.last_name).filter_by(id=q.prospect_id).first()
        if p:
            prospect_name = f"{p.first_name} {p.last_name or ''}".strip()
        stuck_items.append({
            "id": q.id,
            "prospect_name": prospect_name,
            "action_type": q.action_type,
            "hours_stuck": hours_stuck,
        })
    oldest_hours = stuck_items[0]["hours_stuck"] if stuck_items else 0
    stuck = {
        "count": len(stuck_rows),
        "oldest_hours": oldest_hours,
        "items": stuck_items[:20],  # Cap response size
    }

    # ── Worker status ────────────────────────────────────────────────────
    worker_alive = linkedin_worker_alive()
    last_cycle_iso = _last_cycle_at.isoformat() if _last_cycle_at else None
    worker = {
        "alive": worker_alive,
        "last_cycle": last_cycle_iso,
    }

    # ── Recent errors: last 5 failed items ───────────────────────────────
    recent_failed = (
        db.query(LinkedInQueue)
        .filter(LinkedInQueue.status == LinkedInQueueStatus.failed)
        .order_by(LinkedInQueue.processed_at.desc().nullslast())
        .limit(5)
        .all()
    )
    recent_errors = []
    for q in recent_failed:
        recent_errors.append({
            "id": q.id,
            "action_type": q.action_type,
            "result_detail": q.result_detail,
            "created_at": q.created_at.isoformat() if q.created_at else None,
        })

    # ── Alerts: human-readable problem descriptions ──────────────────────
    alerts: list[str] = []

    if stuck["count"] > 0:
        alerts.append(
            f"{stuck['count']} item(s) stuck in processing >2h "
            f"(oldest: {oldest_hours:.1f}h)"
        )

    if not worker_alive:
        alerts.append("LinkedIn worker thread is NOT alive")

    if _last_cycle_at:
        minutes_since_cycle = (now - _last_cycle_at).total_seconds() / 60
        if minutes_since_cycle > 15:
            alerts.append(
                f"Worker not cycling — last cycle was {minutes_since_cycle:.0f}min ago"
            )

    # Check for PB circuit breaker status
    try:
        from ..services.phantombuster import _circuit_open_until
        if _circuit_open_until and now < _circuit_open_until:
            alerts.append(
                f"PhantomBuster circuit breaker is OPEN until {_circuit_open_until.isoformat()}"
            )
    except ImportError:
        pass

    if queue["failed"] > 0 and queue["failed"] > queue["done"]:
        alerts.append(
            f"More failed items ({queue['failed']}) than done ({queue['done']})"
        )

    return {
        "queue": queue,
        "stuck": stuck,
        "worker": worker,
        "recent_errors": recent_errors,
        "alerts": alerts,
    }


@router.get("/linkedin/queue", dependencies=[Depends(require_api_key)])
def get_linkedin_queue(
    status: str = Query(None, description="Filter by status: queued, processing, done, failed"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List items in the LinkedIn batch queue."""
    from ..db.models import LinkedInQueue, LinkedInQueueStatus

    q = db.query(LinkedInQueue).order_by(LinkedInQueue.created_at.desc())
    if status:
        try:
            q = q.filter(LinkedInQueue.status == LinkedInQueueStatus(status))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid status: {status}")
    items = q.limit(limit).all()
    return [
        {
            "id": item.id,
            "prospect_id": item.prospect_id,
            "campaign_id": item.campaign_id,
            "action_type": item.action_type,
            "linkedin_url": item.linkedin_url,
            "message_text": item.message_text,
            "status": item.status.value if item.status else None,
            "phantom_container_id": item.phantom_container_id,
            "result_detail": item.result_detail,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "processed_at": item.processed_at.isoformat() if item.processed_at else None,
        }
        for item in items
    ]
