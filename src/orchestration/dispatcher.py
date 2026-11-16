"""
ActionDispatcher  -  routes sequence steps to the appropriate handler.

The dispatcher sits between the scheduler and the handlers. The scheduler
decides WHICH leads need action and WHEN; the dispatcher decides HOW to
execute each step and handles the aftermath.

FLOW (called from scheduler.execute_next_step):
  1. Generate a deterministic execution_id for idempotency
     (format: {state_id}_{step_id}_{attempt}).
  2. Check if the execution has already been recorded (skip if so).
  3. Select the correct handler based on StepType from _handlers dict.
  4. Call handler.handle() with step, lead context, state, and execution_id.
  5. Handle the result side-effects:
     - executed → reset failure counter (if any), let scheduler advance
     - skipped  → same as executed (condition not met, already done, etc.)
     - failed   → enter retry ladder: reschedule +1h/+6h, or pause after max failures
     - deferred → reschedule 1 hour forward (transient provider issue)
  6. Persist state changes to the database.

IMPORTANT: The dispatcher shares the scheduler's DB session so all changes
(orch state + LinkedIn queue rows + audit logs) commit or rollback together.
This is the P2-4 / CRITICAL-2 fix that prevents split-brain between the
orchestration state and the LinkedIn queue.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from .enums import StepResultStatus, StepType
from .handlers import (
    CreateManualTaskHandler,
    EndSequenceHandler,
    SendEmailRemovedHandler,
    SendLinkedInConnectHandler,
    SendLinkedInMessageHandler,
    WaitHandler,
)
from .models import LeadContext, LeadSequenceState, SequenceStep, StepResult
from .providers import LinkedInProvider, StubLinkedInProvider
from .repositories import (
    ActionExecutionRepository,
    AuditLogRepository,
    LeadSequenceStateRepository,
    ManualTaskRepository,
    ProspectRepository,
    build_execution_id,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


class ActionDispatcher:
    """
    Routes sequence steps to the appropriate handler and manages result side-effects.

    Instantiate once per request/job and reuse across multiple dispatch calls
    within the same DB session.
    """

    def __init__(
        self,
        linkedin_provider: LinkedInProvider,
        db: Session,
        config_registry: Callable[[str], Any],
    ) -> None:
        """
        Args:
            linkedin_provider: Concrete or stub LinkedInProvider.
            db:               SQLAlchemy session.
            config_registry:  Callable that accepts sequence_id and returns SequenceConfig.
                              Typically get_sequence_config from config.py.
        """
        self._linkedin = linkedin_provider
        self._db = db
        self._config_registry = config_registry

        # Repositories  -  shared across all dispatch calls in this session
        self._action_repo = ActionExecutionRepository(db)
        self._audit_repo = AuditLogRepository(db)
        self._task_repo = ManualTaskRepository(db)
        self._prospect_repo = ProspectRepository(db)
        self._state_repo = LeadSequenceStateRepository(db)

        # Build handler instances
        self._handlers: dict[StepType, Any] = {
            # Email removed from campaign outreach (2026-06): send_email steps
            # left in existing sequences are skipped so leads advance to their
            # next LinkedIn step instead of dead-ending. See SendEmailRemovedHandler.
            StepType.send_email: SendEmailRemovedHandler(
                action_repo=self._action_repo,
                audit_repo=self._audit_repo,
            ),
            StepType.send_linkedin_connect: SendLinkedInConnectHandler(
                linkedin_provider=self._linkedin,
                lead_repo=self._prospect_repo,
                action_repo=self._action_repo,
                audit_repo=self._audit_repo,
                db=self._db,  # P2-4 / CRITICAL-2: share scheduler transaction
            ),
            StepType.send_linkedin_message: SendLinkedInMessageHandler(
                linkedin_provider=self._linkedin,
                action_repo=self._action_repo,
                task_repo=self._task_repo,
                audit_repo=self._audit_repo,
                db=self._db,  # P2-4 / CRITICAL-2: share scheduler transaction
            ),
            StepType.create_manual_task: CreateManualTaskHandler(
                task_repo=self._task_repo,
                audit_repo=self._audit_repo,
            ),
            StepType.wait: WaitHandler(
                action_repo=self._action_repo,
                audit_repo=self._audit_repo,
            ),
            StepType.end_sequence: EndSequenceHandler(
                audit_repo=self._audit_repo,
                action_repo=self._action_repo,
            ),
            # Stubs: fall back to manual task creation
            StepType.send_call_task: CreateManualTaskHandler(
                task_repo=self._task_repo,
                audit_repo=self._audit_repo,
            ),
            StepType.send_whatsapp: CreateManualTaskHandler(
                task_repo=self._task_repo,
                audit_repo=self._audit_repo,
            ),
        }

    def dispatch(
        self,
        step: SequenceStep,
        lead: LeadContext,
        state: LeadSequenceState,
    ) -> StepResult:
        """
        Execute a sequence step and handle the result.

        P2-7 / HIGH-6: this method is the "execute or retry an existing
        step" entry point. It always increments the attempt counter from
        the (now-inclusive) `count_attempts()` total, so a failed prior
        attempt produces a fresh execution_id via `build_execution_id()`.
        Compare with `scheduler.execute_next_step` which is the
        "first-attempt fast path" called from the scheduler tick  -  both
        rely on the same helper for execution_id construction.

        Args:
            step:  The SequenceStep to execute.
            lead:  LeadContext for the lead.
            state: Current LeadSequenceState (may be mutated by the handler).

        Returns:
            StepResult describing the outcome.
        """
        # P2-7: count_attempts() now includes failed rows so attempt strictly
        # increments on retry. build_execution_id() is the single source of
        # truth shared with scheduler.execute_next_step.
        attempt = self._action_repo.count_attempts(state.state_id, step.step_id) + 1
        execution_id = build_execution_id(state.state_id, step.step_id, attempt)

        # Idempotency guard
        if self._action_repo.exists(execution_id):
            logger.info(
                "ActionDispatcher.dispatch: execution_id=%s already exists  -  skipping",
                execution_id,
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.skipped,
                execution_id=execution_id,
                detail="idempotent_skip",
            )

        handler = self._handlers.get(step.step_type)
        if handler is None:
            logger.error(
                "ActionDispatcher.dispatch: no handler registered for StepType=%s",
                step.step_type.value,
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.failed,
                execution_id=execution_id,
                detail=f"No handler for step_type={step.step_type.value}",
            )

        try:
            result = handler.handle(
                step=step,
                lead=lead,
                state=state,
                execution_id=execution_id,
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Unhandled exception in handler {type(handler).__name__}: {exc}"
            logger.exception(
                "ActionDispatcher.dispatch: unhandled exception for lead=%s step=%s",
                lead.lead_id,
                step.step_id,
            )
            result = StepResult(
                step_id=step.step_id,
                status=StepResultStatus.failed,
                execution_id=execution_id,
                detail=error_msg,
            )

        # ── Handle result side-effects ──────────────────────────────────────

        if result.status == StepResultStatus.failed:
            self._handle_failure_with_ladder(state, result)

        elif result.status == StepResultStatus.deferred:
            self._reschedule_deferred(state)

        else:
            # P2-2: any non-failure outcome (executed/skipped) resets the
            # consecutive failure counter so a flaky run that recovers
            # doesn't carry old failure debt forward.
            if state.consecutive_failure_count:
                state.consecutive_failure_count = 0
                self._state_repo.upsert_from_pydantic(state)

        return result

    def _handle_failure_with_ladder(
        self,
        state: LeadSequenceState,
        result: StepResult,
    ) -> None:
        """
        P2-2 / deep audit HIGH-4: failure retry ladder.

        Increment `consecutive_failure_count`. For the first
        `len(RETRY_LADDER_HOURS)` failures the sequence is rescheduled
        forward by the matching ladder entry (e.g. [1, 6] → +1h, +6h).
        After `MAX_CONSECUTIVE_FAILURES` failures the sequence is paused
        with `paused_reason` set to the failure detail.

        Args:
            state:  LeadSequenceState to update (mutated in place).
            result: StepResult with the failure detail.
        """
        from ..config import MAX_CONSECUTIVE_FAILURES, RETRY_LADDER_HOURS

        # SWAT-L1: cap failure count to prevent unbounded growth
        state.consecutive_failure_count = min(
            (state.consecutive_failure_count or 0) + 1,
            MAX_CONSECUTIVE_FAILURES + 5,
        )
        count = state.consecutive_failure_count

        if count >= MAX_CONSECUTIVE_FAILURES:
            # Exhausted the ladder  -  pause as before.
            self._pause_sequence_on_failure(state, result)
            return

        # Reschedule using the ladder. count is 1-based; index into the
        # ladder is count-1, clamped to the last entry as a safety net.
        ladder_index = min(count - 1, len(RETRY_LADDER_HOURS) - 1)
        delay_hours = RETRY_LADDER_HOURS[ladder_index]
        new_time = _utcnow() + timedelta(hours=delay_hours)
        state.scheduled_next_at = new_time
        self._state_repo.upsert_from_pydantic(state)
        logger.warning(
            "_handle_failure_with_ladder: lead=%s state=%s failure %d/%d  -  "
            "rescheduled +%dh to %s (detail=%s)",
            state.lead_id,
            state.state_id,
            count,
            MAX_CONSECUTIVE_FAILURES,
            delay_hours,
            new_time.isoformat(),
            result.detail,
        )

    def _pause_sequence_on_failure(
        self,
        state: LeadSequenceState,
        result: StepResult,
    ) -> None:
        """
        Pause the sequence when a step handler returns failed.

        Sets state.status to paused and state.paused_reason to the error detail.
        Writes an audit log entry.

        P2-2: this is now invoked by `_handle_failure_with_ladder` only
        after the retry ladder is exhausted (MAX_CONSECUTIVE_FAILURES).

        Args:
            state:  LeadSequenceState to pause (mutated in place).
            result: StepResult with the failure detail.
        """
        from .state_machine import is_terminal, transition_state

        if is_terminal(state.status):
            return  # Already terminal  -  don't try to pause

        try:
            transition_state(
                state=state,
                trigger="pause",
                event=None,
                audit_repo=self._audit_repo,
            )
            state.paused_reason = result.detail or "step_execution_failed"
        except ValueError as exc:
            logger.warning(
                "_pause_sequence_on_failure: could not pause state=%s: %s",
                state.state_id,
                exc,
            )
            return

        self._state_repo.upsert_from_pydantic(state)
        logger.warning(
            "Sequence paused: lead=%s state=%s reason=%s",
            state.lead_id,
            state.state_id,
            state.paused_reason,
        )

    def _reschedule_deferred(self, state: LeadSequenceState) -> None:
        """
        Reschedule the state 1 hour forward when a step is deferred.

        Args:
            state: LeadSequenceState to reschedule (mutated in place).
        """
        new_time = _utcnow() + timedelta(hours=1)
        state.scheduled_next_at = new_time
        self._state_repo.upsert_from_pydantic(state)
        logger.info(
            "_reschedule_deferred: lead=%s state=%s rescheduled to %s",
            state.lead_id,
            state.state_id,
            new_time.isoformat(),
        )
