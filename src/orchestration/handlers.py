"""
Step handlers  -  one class per StepType.

Each handler implements a handle() method that performs the actual action for
a sequence step and returns a StepResult. These are the "leaf nodes" of the
orchestration engine — the point where business logic meets external providers.

HANDLER RESPONSIBILITIES:
  - Call the appropriate provider (or queue an action for batch processing)
  - Catch all provider exceptions and convert to StepResultStatus.failed
  - Write an ActionExecution record (via _save_execution helper)
  - Handle edge cases: stub providers, missing data, already-connected, etc.
  - Return a StepResult so the dispatcher knows what happened

HANDLER CLASSES:
  - SendLinkedInConnectHandler: queues a connection request to linkedin_queue
  - SendLinkedInMessageHandler: queues a DM to linkedin_queue (or creates manual task fallback)
  - SendEmailRemovedHandler: no-op skip for legacy email steps (email removed 2026-06)
  - CreateManualTaskHandler: creates a task for the sales rep in orch_manual_tasks
  - WaitHandler: no-op; timing is handled by the scheduler's day_offset scheduling
  - EndSequenceHandler: triggers the "sequence_ended" FSM transition → completed

IMPORTANT: LinkedIn handlers do NOT call PhantomBuster directly. They queue
actions to the linkedin_queue table via queue_linkedin_action_no_commit().
The linkedin_worker.py background thread then drains the queue, pushes leads
to PhantomBuster's CSV feed, and polls for results asynchronously.

Handlers are NOT registered via a static dict — they're instantiated by
ActionDispatcher.__init__ in dispatcher.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .enums import ProviderStatus, StepResultStatus, StepType, TaskPriority
from .models import (
    ActionExecution,
    AuditLog,
    LeadContext,
    LeadSequenceState,
    ManualTask,
    SequenceStep,
    StepResult,
)
from .providers import (
    LinkedInProvider,
    StubLinkedInProvider,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def _save_execution(
    action_repo: Any,
    audit_repo: Any,
    execution_id: str,
    lead_id: str,
    step_id: str,
    action_type: StepType,
    status: StepResultStatus,
    state_id: str,
    result_payload: dict | None = None,
    error_message: str | None = None,
) -> None:
    """
    Persist an ActionExecution and a corresponding AuditLog entry.

    Args:
        action_repo:    ActionExecutionRepository.
        audit_repo:     AuditLogRepository.
        execution_id:   Idempotency key.
        lead_id:        Lead identifier.
        step_id:        Step identifier.
        action_type:    StepType value.
        status:         StepResultStatus of this execution attempt.
        state_id:       Sequence state identifier.
        result_payload: Optional dict with provider response.
        error_message:  Optional error message if status is failed.
    """
    execution = ActionExecution(
        execution_id=execution_id,
        lead_id=lead_id,
        step_id=step_id,
        action_type=action_type,
        status=status,
        attempted_at=_utcnow(),
        result_payload=result_payload,
        error_message=error_message,
        state_id=state_id,
    )
    action_repo.save(execution)

    log = AuditLog(
        lead_id=lead_id,
        state_id=state_id,
        entity_type="action_execution",
        entity_id=execution_id,
        action=f"step_{status.value}",
        detail={
            "step_id": step_id,
            "action_type": action_type.value,
            "status": status.value,
            "error": error_message,
        },
        created_at=_utcnow(),
    )
    audit_repo.write(log)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class StepHandler:
    """Base class / protocol for all step handlers."""

    def handle(
        self,
        step: SequenceStep,
        lead: LeadContext,
        state: LeadSequenceState,
        execution_id: str,
    ) -> StepResult:
        """
        Execute the step action and return a StepResult.

        Subclasses must override this method.

        Args:
            step:         The sequence step to execute.
            lead:         Current lead context snapshot.
            state:        Current sequence state.
            execution_id: Idempotency key for this execution attempt.

        Returns:
            StepResult describing the outcome.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# LinkedIn connect handler
# ---------------------------------------------------------------------------


class SendLinkedInConnectHandler(StepHandler):
    """
    Handle send_linkedin_connect steps.

    Edge cases handled inline:
      1. StubLinkedInProvider: log warning, return skipped (never fail on stub).
      2. No linkedin_url on lead: return skipped.
      3. Already connected: return skipped (idempotent).
      4. QUOTA_EXCEEDED: return deferred (dispatcher reschedules 1h later).

    P2-4 / deep audit CRITICAL-2: this handler participates in the scheduler
    transaction. It uses `self._db` (the parent session) and
    `queue_linkedin_action_no_commit()` so the LinkedIn queue row and the
    orchestration state share a commit boundary.
    """

    def __init__(
        self,
        linkedin_provider: LinkedInProvider,
        lead_repo: Any,
        action_repo: Any,
        audit_repo: Any,
        db: Any = None,
    ) -> None:
        """
        Args:
            linkedin_provider: Concrete or stub LinkedInProvider.
            lead_repo:         ProspectRepository (for updating connection state).
            action_repo:       ActionExecutionRepository.
            audit_repo:        AuditLogRepository.
            db:                SQLAlchemy session the dispatcher already owns.
                               Required for P2-4 fix  -  see class docstring.
        """
        self._li = linkedin_provider
        self._lead_repo = lead_repo
        self._action_repo = action_repo
        self._audit_repo = audit_repo
        self._db = db

    def handle(
        self,
        step: SequenceStep,
        lead: LeadContext,
        state: LeadSequenceState,
        execution_id: str,
    ) -> StepResult:
        """
        Send a LinkedIn connection request.

        Args:
            step:         Sequence step.
            lead:         Lead context.
            state:        Sequence state.
            execution_id: Idempotency key.

        Returns:
            StepResult.
        """
        # Edge case 1: stub provider  -  skip gracefully
        if isinstance(self._li, StubLinkedInProvider):
            logger.warning(
                "SendLinkedInConnectHandler: StubLinkedInProvider detected for lead=%s  -  skipping step",
                lead.lead_id,
            )
            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_connect, StepResultStatus.skipped,
                state.state_id,
                result_payload={"reason": "stub_provider"},
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.skipped,
                execution_id=execution_id,
                detail="LinkedIn provider is a stub  -  not connected",
            )

        # Edge case 2: no LinkedIn URL
        if not lead.linkedin_url:
            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_connect, StepResultStatus.skipped,
                state.state_id,
                result_payload={"reason": "no_linkedin_url"},
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.skipped,
                execution_id=execution_id,
                detail="No LinkedIn URL on lead profile",
            )

        # Edge case 3: already connected  -  idempotency
        if lead.linkedin_connected:
            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_connect, StepResultStatus.skipped,
                state.state_id,
                result_payload={"reason": "already_connected"},
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.skipped,
                execution_id=execution_id,
                detail="Lead is already a LinkedIn connection",
            )

        # P2-4: queue for batch processing using the dispatcher's session.
        # DO NOT open a fresh SessionLocal  -  the scheduler owns the commit.
        if self._db is None:
            error_msg = (
                "SendLinkedInConnectHandler: parent session missing  -  "
                "dispatcher must construct this handler with db=self._db"
            )
            logger.error(error_msg)
            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_connect, StepResultStatus.failed,
                state.state_id, error_message=error_msg,
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.failed,
                execution_id=execution_id,
                detail=error_msg,
            )

        try:
            from ..services.linkedin_batch import queue_linkedin_action_no_commit
            from ..db.models import EmailSequence

            # --- Connect note resolution ---
            # The connect note is a personalised message attached to the LinkedIn
            # connection request. It's generated by Claude during campaign launch
            # and stored in EmailSequence.linkedin_connect_note.
            #
            # Resolution order (most specific → least specific):
            #   1. EmailSequence for this prospect + campaign (exact match)
            #   2. Most recent EmailSequence for this prospect (any campaign)
            #   3. ConferenceLead.generated_sequence JSON (conference outreach path)
            #   4. Empty string (LinkedIn allows blank connect notes)
            campaign_id = (getattr(state, "metadata", None) or {}).get("campaign_id")
            actual_note = ""
            seq = None
            if campaign_id:
                seq = (
                    self._db.query(EmailSequence)
                    .filter_by(prospect_id=lead.lead_id, campaign_id=campaign_id)
                    .first()
                )
                if seq and seq.linkedin_connect_note:
                    actual_note = seq.linkedin_connect_note
            if not actual_note:
                # Fallback: try most recent sequence for this prospect
                seq = (
                    self._db.query(EmailSequence)
                    .filter_by(prospect_id=lead.lead_id)
                    .order_by(EmailSequence.created_at.desc())
                    .first()
                )
                if seq and seq.linkedin_connect_note:
                    actual_note = seq.linkedin_connect_note
            if not actual_note:
                # Fallback: ConferenceLead (pre-conference outreach uses a
                # separate data model with generated_sequence as a JSON blob)
                from ..db.models import ConferenceLead
                import json as _json
                conf_lead = self._db.query(ConferenceLead).filter_by(id=lead.lead_id).first()
                if conf_lead and conf_lead.generated_sequence:
                    seq_data = _json.loads(conf_lead.generated_sequence) if isinstance(conf_lead.generated_sequence, str) else (conf_lead.generated_sequence or {})
                    actual_note = seq_data.get("linkedin_connect_note", "")
            # Resolve campaign_id from the sequence if not set from state metadata
            if not campaign_id and seq:
                campaign_id = seq.campaign_id

            # --- User ID resolution ---
            # The user_id is needed on the queue row so the linkedin_worker can:
            #   (a) enforce per-user daily LinkedIn connect caps
            #   (b) use the correct PhantomBuster identity for multi-user deployments
            # Resolution: lead.user_id → Campaign.user_id → ConferenceLead.user_id
            user_id = getattr(lead, "user_id", None)
            if not user_id and campaign_id:
                from ..db.models import Campaign
                camp = self._db.query(Campaign.user_id).filter_by(id=campaign_id).first()
                if camp:
                    user_id = camp[0]
            if not user_id:
                # Fallback: resolve from ConferenceLead
                from ..db.models import ConferenceLead as _CL
                _cl = self._db.query(_CL.user_id).filter_by(id=lead.lead_id).first()
                if _cl:
                    user_id = _cl[0]

            # --- LinkedIn account resolution ---
            # If the campaign has an outreach_account_id, use it so the
            # linkedin_worker routes this action to the correct PB phantom.
            # Falls back to None (legacy env-var resolution in the worker).
            linkedin_account_id = None
            if campaign_id:
                from ..db.models import Campaign as _Camp
                _c = self._db.query(_Camp.outreach_account_id).filter_by(id=campaign_id).first()
                if _c and _c[0]:
                    linkedin_account_id = _c[0]

            # Queue the connect action — does NOT commit (scheduler owns the transaction).
            # The linkedin_worker background thread picks this up, marks it "processing",
            # pushes it into PB's CSV feed, launches the phantom, and polls for results.
            queue_linkedin_action_no_commit(
                db=self._db,
                prospect_id=lead.lead_id,
                campaign_id=campaign_id,
                action_type="connect",
                linkedin_url=lead.linkedin_url,
                message_text=actual_note,
                user_id=user_id,
                linkedin_account_id=linkedin_account_id,
            )

            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_connect, StepResultStatus.executed,
                state.state_id,
                result_payload={"queued": True, "batch_mode": True, "linkedin_account_id": linkedin_account_id},
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.executed,
                execution_id=execution_id,
                detail="LinkedIn connect queued for batch processing",
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = f"LinkedIn queue error: {exc}"
            import httpx
            # Distinguish transient errors (network issues → deferred, retry in 1h)
            # from permanent errors (logic bugs → failed, enters retry ladder).
            # Transient errors don't count against the failure ladder because they'll
            # likely resolve on their own.
            _transient = isinstance(exc, (
                ConnectionError, TimeoutError, OSError,
                httpx.ConnectError, httpx.TimeoutException,
                httpx.RemoteProtocolError,
            ))
            _result_status = StepResultStatus.deferred if _transient else StepResultStatus.failed
            _log_fn = logger.warning if _transient else logger.error
            _log_fn(
                "SendLinkedInConnectHandler queue error for lead %s (transient=%s): %s",
                lead.lead_id, _transient, error_msg,
            )
            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_connect, _result_status,
                state.state_id, error_message=error_msg,
            )
            return StepResult(
                step_id=step.step_id,
                status=_result_status,
                execution_id=execution_id,
                detail=error_msg,
            )


# ---------------------------------------------------------------------------
# LinkedIn message handler
# ---------------------------------------------------------------------------


class SendLinkedInMessageHandler(StepHandler):
    """
    Handle send_linkedin_message steps.

    If the linkedin_connected condition has already been evaluated and failed
    before this handler is called, the dispatcher should have already skipped
    this step.  However, as a safety net this handler also checks and creates
    a manual task if not connected.
    """

    def __init__(
        self,
        linkedin_provider: LinkedInProvider,
        action_repo: Any,
        task_repo: Any,
        audit_repo: Any,
        db: Any = None,
    ) -> None:
        """
        Args:
            linkedin_provider: Concrete or stub LinkedInProvider.
            action_repo:       ActionExecutionRepository.
            task_repo:         ManualTaskRepository.
            audit_repo:        AuditLogRepository.
            db:                Parent SQLAlchemy session (P2-4 / CRITICAL-2).
                               Required so the LinkedIn queue row and the
                               orchestration state share a commit boundary.
        """
        self._li = linkedin_provider
        self._action_repo = action_repo
        self._task_repo = task_repo
        self._audit_repo = audit_repo
        self._db = db

    def handle(
        self,
        step: SequenceStep,
        lead: LeadContext,
        state: LeadSequenceState,
        execution_id: str,
    ) -> StepResult:
        """
        Send a LinkedIn direct message.  Falls back to manual task if not connected.

        Args:
            step:         Sequence step.
            lead:         Lead context.
            state:        Sequence state.
            execution_id: Idempotency key.

        Returns:
            StepResult.
        """
        # Stub provider: skip
        if isinstance(self._li, StubLinkedInProvider):
            logger.warning(
                "SendLinkedInMessageHandler: StubLinkedInProvider for lead=%s  -  creating manual task fallback",
                lead.lead_id,
            )
            self._create_fallback_task(lead, state, "LinkedIn provider not configured")
            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_message, StepResultStatus.skipped,
                state.state_id,
                result_payload={"reason": "stub_provider_fallback_task_created"},
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.skipped,
                execution_id=execution_id,
                detail="Stub LinkedIn provider  -  manual task created as fallback",
            )

        # Not connected: create manual task instead
        if not lead.linkedin_connected:
            self._create_fallback_task(lead, state, "Not connected on LinkedIn at day 16")
            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_message, StepResultStatus.skipped,
                state.state_id,
                result_payload={"reason": "not_connected_fallback_task_created"},
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.skipped,
                execution_id=execution_id,
                detail="Not connected on LinkedIn  -  manual task created as fallback",
            )

        if not lead.linkedin_url:
            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_message, StepResultStatus.skipped,
                state.state_id,
                result_payload={"reason": "no_linkedin_url"},
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.skipped,
                execution_id=execution_id,
                detail="No LinkedIn URL on lead",
            )

        # P2-4: queue for batch processing using the dispatcher's session.
        # DO NOT open a fresh SessionLocal  -  scheduler owns the commit.
        if self._db is None:
            error_msg = (
                "SendLinkedInMessageHandler: parent session missing  -  "
                "dispatcher must construct this handler with db=self._db"
            )
            logger.error(error_msg)
            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_message, StepResultStatus.failed,
                state.state_id, error_message=error_msg,
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.failed,
                execution_id=execution_id,
                detail=error_msg,
            )

        try:
            from ..services.linkedin_batch import queue_linkedin_action_no_commit
            from ..db.models import EmailSequence

            # Resolve the actual personalized message from EmailSequence
            campaign_id = (getattr(state, "metadata", None) or {}).get("campaign_id")
            actual_message = ""
            seq = None
            if campaign_id:
                seq = (
                    self._db.query(EmailSequence)
                    .filter_by(prospect_id=lead.lead_id, campaign_id=campaign_id)
                    .first()
                )
                if seq and seq.linkedin_message:
                    actual_message = seq.linkedin_message
            if not actual_message:
                seq = (
                    self._db.query(EmailSequence)
                    .filter_by(prospect_id=lead.lead_id)
                    .order_by(EmailSequence.created_at.desc())
                    .first()
                )
                if seq and seq.linkedin_message:
                    actual_message = seq.linkedin_message
            if not actual_message:
                # Fallback: ConferenceLead (pre-conference outreach)
                from ..db.models import ConferenceLead
                import json as _json
                conf_lead = self._db.query(ConferenceLead).filter_by(id=lead.lead_id).first()
                if conf_lead and conf_lead.generated_sequence:
                    seq_data = _json.loads(conf_lead.generated_sequence) if isinstance(conf_lead.generated_sequence, str) else (conf_lead.generated_sequence or {})
                    actual_message = seq_data.get("linkedin_message", "")
            # Resolve campaign_id from the sequence if not set from state metadata
            if not campaign_id and seq:
                campaign_id = seq.campaign_id

            user_id = getattr(lead, "user_id", None)
            if not user_id and campaign_id:
                from ..db.models import Campaign
                camp = self._db.query(Campaign.user_id).filter_by(id=campaign_id).first()
                if camp:
                    user_id = camp[0]
            if not user_id:
                # Fallback: resolve from ConferenceLead
                from ..db.models import ConferenceLead as _CL
                _cl = self._db.query(_CL.user_id).filter_by(id=lead.lead_id).first()
                if _cl:
                    user_id = _cl[0]

            # --- LinkedIn account resolution ---
            # If the campaign has an outreach_account_id, use it so the
            # linkedin_worker routes this action to the correct PB phantom.
            # Falls back to None (legacy env-var resolution in the worker).
            linkedin_account_id = None
            if campaign_id:
                from ..db.models import Campaign as _Camp
                _c = self._db.query(_Camp.outreach_account_id).filter_by(id=campaign_id).first()
                if _c and _c[0]:
                    linkedin_account_id = _c[0]

            queue_linkedin_action_no_commit(
                db=self._db,
                prospect_id=lead.lead_id,
                campaign_id=campaign_id,
                action_type="message",
                linkedin_url=lead.linkedin_url,
                message_text=actual_message,
                user_id=user_id,
                linkedin_account_id=linkedin_account_id,
            )

            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_message, StepResultStatus.executed,
                state.state_id,
                result_payload={"queued": True, "batch_mode": True, "linkedin_account_id": linkedin_account_id},
            )
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.executed,
                execution_id=execution_id,
                detail="LinkedIn message queued for batch processing",
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = f"LinkedIn queue error: {exc}"
            import httpx
            _transient = isinstance(exc, (
                ConnectionError, TimeoutError, OSError,
                httpx.ConnectError, httpx.TimeoutException,
                httpx.RemoteProtocolError,
            ))
            _result_status = StepResultStatus.deferred if _transient else StepResultStatus.failed
            _log_fn = logger.warning if _transient else logger.error
            _log_fn(
                "SendLinkedInMessageHandler queue error for lead %s (transient=%s): %s",
                lead.lead_id, _transient, error_msg,
            )
            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.send_linkedin_message, _result_status,
                state.state_id, error_message=error_msg,
            )
            return StepResult(
                step_id=step.step_id,
                status=_result_status,
                execution_id=execution_id,
                detail=error_msg,
            )

    def _create_fallback_task(
        self,
        lead: LeadContext,
        state: LeadSequenceState,
        reason: str,
    ) -> None:
        """Create a manual task when LinkedIn messaging cannot proceed."""
        from datetime import timedelta

        task = ManualTask(
            lead_id=lead.lead_id,
            reason=f"LinkedIn message fallback: {reason}",
            priority=TaskPriority.medium,
            suggested_action=(
                f"Send a personalised LinkedIn message to {lead.first_name} {lead.last_name} "
                f"at {lead.company}.  Template: linkedin_message_v1."
            ),
            due_date=_utcnow() + timedelta(days=2),
        )
        self._task_repo.save(task)
        log = AuditLog(
            lead_id=lead.lead_id,
            state_id=state.state_id,
            entity_type="manual_task",
            entity_id=task.task_id,
            action="task_created",
            detail={"reason": reason, "task_id": task.task_id},
            created_at=_utcnow(),
        )
        self._audit_repo.write(log)


# ---------------------------------------------------------------------------
# Manual task handler
# ---------------------------------------------------------------------------


class CreateManualTaskHandler(StepHandler):
    """Handle create_manual_task steps by creating a ManualTask record."""

    def __init__(self, task_repo: Any, audit_repo: Any) -> None:
        """
        Args:
            task_repo:  ManualTaskRepository.
            audit_repo: AuditLogRepository.
        """
        self._task_repo = task_repo
        self._audit_repo = audit_repo

    def handle(
        self,
        step: SequenceStep,
        lead: LeadContext,
        state: LeadSequenceState,
        execution_id: str,
    ) -> StepResult:
        """
        Create a manual task for the sales rep.

        Args:
            step:         Sequence step.
            lead:         Lead context.
            state:        Sequence state.
            execution_id: Idempotency key.

        Returns:
            StepResult.
        """
        from datetime import timedelta

        task = ManualTask(
            lead_id=lead.lead_id,
            reason=f"Manual task from sequence step {step.step_id}",
            priority=TaskPriority.medium,
            suggested_action=(
                f"Review lead {lead.first_name} {lead.last_name} at {lead.company} "
                f"and take the appropriate next action."
            ),
            due_date=_utcnow() + timedelta(days=2),
        )

        try:
            self._task_repo.save(task)
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Failed to create manual task: {exc}"
            logger.error("CreateManualTaskHandler error for lead %s: %s", lead.lead_id, error_msg)
            return StepResult(
                step_id=step.step_id,
                status=StepResultStatus.failed,
                execution_id=execution_id,
                detail=error_msg,
            )

        log = AuditLog(
            lead_id=lead.lead_id,
            state_id=state.state_id,
            entity_type="manual_task",
            entity_id=task.task_id,
            action="task_created",
            detail={"step_id": step.step_id, "task_id": task.task_id},
            created_at=_utcnow(),
        )
        self._audit_repo.write(log)

        return StepResult(
            step_id=step.step_id,
            status=StepResultStatus.executed,
            execution_id=execution_id,
            detail=f"Manual task {task.task_id} created",
        )


# ---------------------------------------------------------------------------
# Wait handler
# ---------------------------------------------------------------------------


class WaitHandler(StepHandler):
    """
    No-op handler for wait steps.

    Wait steps are used to insert deliberate pauses in a sequence without
    requiring any action.  The scheduler's scheduling logic handles the
    timing; this handler simply marks the step as executed.
    """

    def __init__(self, action_repo: Any = None, audit_repo: Any = None) -> None:
        """
        Args:
            action_repo: ActionExecutionRepository (optional for backwards compat).
            audit_repo:  AuditLogRepository (optional for backwards compat).
        """
        self._action_repo = action_repo
        self._audit_repo = audit_repo

    def handle(
        self,
        step: SequenceStep,
        lead: LeadContext,
        state: LeadSequenceState,
        execution_id: str,
    ) -> StepResult:
        """
        Mark the wait step as executed and write an audit record (Fix 18).

        Args:
            step:         Sequence step.
            lead:         Lead context.
            state:        Sequence state.
            execution_id: Idempotency key.

        Returns:
            StepResult with status=executed.
        """
        # Fix 18: Persist an audit/execution record for wait steps
        if self._action_repo is not None and self._audit_repo is not None:
            _save_execution(
                self._action_repo, self._audit_repo,
                execution_id, lead.lead_id, step.step_id,
                StepType.wait, StepResultStatus.executed,
                state.state_id,
                result_payload={"detail": "wait_step_executed"},
            )
        return StepResult(
            step_id=step.step_id,
            status=StepResultStatus.executed,
            execution_id=execution_id,
            detail="Wait step  -  no action taken",
        )


# ---------------------------------------------------------------------------
# Send-email handler (email removed from campaign outreach, 2026-06)
# ---------------------------------------------------------------------------


class SendEmailRemovedHandler(StepHandler):
    """
    No-op handler for ``send_email`` steps.

    Email was removed from campaign outreach in June 2026 — all campaign
    outreach now runs through LinkedIn (PhantomBuster). Conference/event
    emails use the separate Outlook/Graph path, not the orchestration
    scheduler. Existing sequence configs may still contain ``send_email``
    steps; rather than dead-ending those leads (the handler was deleted in
    the Instantly-removal refactor, which made every email step fail forever),
    this handler treats the step as an inert skip so the lead advances
    straight to its next LinkedIn step.
    """

    def __init__(self, action_repo: Any = None, audit_repo: Any = None) -> None:
        """
        Args:
            action_repo: ActionExecutionRepository (for the audit/skip record).
            audit_repo:  AuditLogRepository.
        """
        self._action_repo = action_repo
        self._audit_repo = audit_repo

    def handle(
        self,
        step: SequenceStep,
        lead: LeadContext,
        state: LeadSequenceState,
        execution_id: str,
    ) -> StepResult:
        """
        Skip the email step without sending and let the scheduler advance.

        Returns a ``skipped`` result (not ``executed``) so the step is not
        recorded as a real send and does not count against daily send limits.
        """
        if self._action_repo is not None and self._audit_repo is not None:
            _save_execution(
                self._action_repo,
                self._audit_repo,
                execution_id,
                lead.lead_id,
                step.step_id,
                StepType.send_email,
                StepResultStatus.skipped,
                state.state_id,
                result_payload={"detail": "email_removed_from_sequence"},
            )
        logger.info(
            "SendEmailRemovedHandler: skipping send_email step %s for lead=%s "
            "(email removed  -  LinkedIn-only outreach)",
            step.step_id,
            lead.lead_id,
        )
        return StepResult(
            step_id=step.step_id,
            status=StepResultStatus.skipped,
            execution_id=execution_id,
            detail="email step removed  -  skipped (LinkedIn-only outreach)",
        )


# ---------------------------------------------------------------------------
# End sequence handler
# ---------------------------------------------------------------------------


class EndSequenceHandler(StepHandler):
    """
    Handle end_sequence steps by triggering the sequence_ended FSM transition.
    """

    def __init__(self, audit_repo: Any, action_repo: Any = None) -> None:
        """
        Args:
            audit_repo: AuditLogRepository (passed to state_machine.transition_state).
            action_repo: ActionExecutionRepository (for saving execution records).
        """
        self._audit_repo = audit_repo
        self._action_repo = action_repo

    def handle(
        self,
        step: SequenceStep,
        lead: LeadContext,
        state: LeadSequenceState,
        execution_id: str,
    ) -> StepResult:
        """
        Trigger the sequence_ended FSM transition.

        The caller (dispatcher / scheduler) is responsible for persisting the
        updated state after this call returns.

        Args:
            step:         Sequence step.
            lead:         Lead context.
            state:        Sequence state (mutated by state_machine).
            execution_id: Idempotency key.

        Returns:
            StepResult with status=executed.
        """
        from .state_machine import transition_state

        try:
            transition_state(
                state=state,
                trigger="sequence_ended",
                event=None,
                audit_repo=self._audit_repo,
            )
        except ValueError as exc:
            logger.warning(
                "EndSequenceHandler: could not transition state for lead=%s: %s",
                lead.lead_id,
                exc,
            )

        # Save execution record for audit completeness
        if self._action_repo:
            _save_execution(
                action_repo=self._action_repo,
                audit_repo=self._audit_repo,
                execution_id=execution_id,
                lead_id=lead.lead_id,
                step_id=step.step_id,
                action_type=step.step_type,
                status=StepResultStatus.executed,
                state_id=state.state_id,
                result_payload={"detail": "Sequence ended"},
            )

        return StepResult(
            step_id=step.step_id,
            status=StepResultStatus.executed,
            execution_id=execution_id,
            detail="Sequence ended",
        )



# Note: handler registration is done dynamically by ActionDispatcher.__init__,
# not via a static registry.  See dispatcher.py.
