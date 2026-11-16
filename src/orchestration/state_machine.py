"""
Explicit finite-state machine (FSM) for lead sequence lifecycle transitions.

This is the core state management layer of the orchestration engine. It controls
how a lead's status changes as they progress through a sequence.

ARCHITECTURE:
  - TRANSITION_TABLE defines every legal (from_status, trigger) → to_status mapping
  - transition_state() is the ONLY way to change a lead's OrchestrationStatus
  - Every transition writes an immutable AuditLog entry for full traceability
  - STOP_EVENT_MAP links inbound events to stop triggers (reply → stop sequence)

The FSM is intentionally table-driven: all valid transitions are declared in
TRANSITION_TABLE so it's easy to audit and extend without touching control flow
code. An invalid transition raises ValueError rather than silently succeeding.

WHO CALLS THIS:
  - scheduler.py: "step_due" (wake from waiting), "step_executed" (advance), "sequence_ended"
  - dispatcher.py: "pause" (on failure ladder exhaustion)
  - ingestion.py: stop events via apply_event_to_state() (reply, unsubscribe, etc.)
  - handlers.py: EndSequenceHandler triggers "sequence_ended"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .enums import EventType, OrchestrationStatus
from .models import AuditLog, EngagementEvent, LeadSequenceState

if TYPE_CHECKING:
    from .repositories import AuditLogRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

# Keys:  (from_status, trigger) → to_status
#
# The happy path for a lead is:
#   pending → (enrolled) → active → (step_executed) → waiting_for_next_step
#   → (step_due) → active → ... → (sequence_ended) → completed
#
# Stop events can interrupt from ANY non-terminal status (active, waiting, paused, pending).
# This ensures a reply received during any phase always terminates the sequence.
TRANSITION_TABLE: dict[tuple[OrchestrationStatus, str], OrchestrationStatus] = {
    # Normal progression: the scheduler drives this cycle
    (OrchestrationStatus.pending,               "enrolled"):            OrchestrationStatus.active,
    (OrchestrationStatus.active,                "step_executed"):       OrchestrationStatus.waiting_for_next_step,
    (OrchestrationStatus.waiting_for_next_step, "step_due"):            OrchestrationStatus.active,

    # Pause / resume (sales team intervention)
    (OrchestrationStatus.active,                "pause"):               OrchestrationStatus.paused,
    (OrchestrationStatus.paused,                "resume"):              OrchestrationStatus.active,

    # Completion
    (OrchestrationStatus.waiting_for_next_step, "sequence_ended"):      OrchestrationStatus.completed,
    (OrchestrationStatus.active,                "sequence_ended"):      OrchestrationStatus.completed,

    # Stop conditions  -  from active
    (OrchestrationStatus.active, "event_replied_positive"):   OrchestrationStatus.stopped_positive_reply,
    (OrchestrationStatus.active, "event_replied_negative"):   OrchestrationStatus.stopped_negative_reply,
    (OrchestrationStatus.active, "event_unsubscribed"):       OrchestrationStatus.stopped_unsubscribed,
    (OrchestrationStatus.active, "event_bounced"):            OrchestrationStatus.stopped_bounced,
    (OrchestrationStatus.active, "event_disqualified"):       OrchestrationStatus.stopped_disqualified,

    # Pause from waiting_for_next_step (Fix 1)
    (OrchestrationStatus.waiting_for_next_step, "pause"):                   OrchestrationStatus.paused,

    # Stop conditions  -  from waiting_for_next_step
    (OrchestrationStatus.waiting_for_next_step, "event_replied_positive"):  OrchestrationStatus.stopped_positive_reply,
    (OrchestrationStatus.waiting_for_next_step, "event_replied_negative"):  OrchestrationStatus.stopped_negative_reply,
    (OrchestrationStatus.waiting_for_next_step, "event_unsubscribed"):      OrchestrationStatus.stopped_unsubscribed,
    (OrchestrationStatus.waiting_for_next_step, "event_bounced"):           OrchestrationStatus.stopped_bounced,
    (OrchestrationStatus.waiting_for_next_step, "event_disqualified"):      OrchestrationStatus.stopped_disqualified,

    # Stop conditions  -  from paused (Fix 1 / Fix 5)
    (OrchestrationStatus.paused, "event_replied_positive"):  OrchestrationStatus.stopped_positive_reply,
    (OrchestrationStatus.paused, "event_replied_negative"):  OrchestrationStatus.stopped_negative_reply,
    (OrchestrationStatus.paused, "event_unsubscribed"):      OrchestrationStatus.stopped_unsubscribed,
    (OrchestrationStatus.paused, "event_bounced"):           OrchestrationStatus.stopped_bounced,
    (OrchestrationStatus.paused, "event_disqualified"):      OrchestrationStatus.stopped_disqualified,

    # Stop conditions  -  from pending (e.g. webhook arrives before enrollment completes)
    (OrchestrationStatus.pending, "event_replied_positive"):  OrchestrationStatus.stopped_positive_reply,
    (OrchestrationStatus.pending, "event_replied_negative"):  OrchestrationStatus.stopped_negative_reply,
    (OrchestrationStatus.pending, "event_unsubscribed"):      OrchestrationStatus.stopped_unsubscribed,
    (OrchestrationStatus.pending, "event_bounced"):           OrchestrationStatus.stopped_bounced,
    (OrchestrationStatus.pending, "event_disqualified"):      OrchestrationStatus.stopped_disqualified,
}

# Mapping from EventType to the trigger string that drives a stop transition.
#
# Channel-level suppression note (2026-04-10):
#   email_bounced_hard is intentionally NOT mapped here. A hard bounce
#   disables the email channel for that prospect but the sequence must
#   still progress on other channels (LinkedIn connect, LinkedIn message,
#   manual task, etc.). The EmailSequence.status column is set to "bounced"
#   by the ingestion bounce handler, and SendEmailHandler reads that at
#   execution time and returns StepResultStatus.skipped without advancing
#   to stopped_bounced. The stopped_bounced transition still exists in
#   TRANSITION_TABLE so the mass-stop "bounce circuit breaker" (which
#   pauses the whole campaign) and manual admin actions can still use it.
#   email_bounced_soft is also excluded (transient / retry).
STOP_EVENT_MAP: dict[EventType, str] = {
    EventType.email_replied_positive: "event_replied_positive",
    EventType.email_replied_negative: "event_replied_negative",
    EventType.unsubscribed:           "event_unsubscribed",
    EventType.lead_disqualified:      "event_disqualified",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_terminal(status: OrchestrationStatus) -> bool:
    """
    Return True if the status represents a terminal state (no further steps).

    Args:
        status: The OrchestrationStatus to check.

    Returns:
        True for all stopped_* and completed statuses.
    """
    return status in (
        OrchestrationStatus.stopped_positive_reply,
        OrchestrationStatus.stopped_negative_reply,
        OrchestrationStatus.stopped_unsubscribed,
        OrchestrationStatus.stopped_bounced,
        OrchestrationStatus.stopped_disqualified,
        OrchestrationStatus.completed,
    )


def _write_audit(
    state: LeadSequenceState,
    trigger: str,
    from_status: OrchestrationStatus,
    to_status: OrchestrationStatus,
    event: EngagementEvent | None,
    audit_repo: AuditLogRepository,
) -> None:
    """
    Append an audit log entry for a state transition.

    Args:
        state:       The sequence state being transitioned.
        trigger:     The trigger string that caused the transition.
        from_status: Status before the transition.
        to_status:   Status after the transition.
        event:       Originating engagement event, if any.
        audit_repo:  Repository to write to.
    """
    detail: dict = {"trigger": trigger}
    if event is not None:
        detail["event_type"] = event.event_type.value
        detail["event_id"] = event.event_id

    log = AuditLog(
        lead_id=state.lead_id,
        state_id=state.state_id,
        entity_type="sequence_state",
        entity_id=state.state_id,
        action="status_transition",
        from_status=from_status.value,
        to_status=to_status.value,
        trigger=trigger,
        detail=detail,
        created_at=datetime.now(timezone.utc),
    )
    audit_repo.write(log)


# ---------------------------------------------------------------------------
# Core transition function
# ---------------------------------------------------------------------------


def transition_state(
    state: LeadSequenceState,
    trigger: str,
    event: EngagementEvent | None,
    audit_repo: AuditLogRepository,
) -> LeadSequenceState:
    """
    Apply a trigger to a sequence state and return the updated state.

    The function is pure with respect to the state model: it returns a new
    LeadSequenceState (or mutates the existing one  -  since Pydantic models are
    mutable by default) and writes an audit log entry.

    Args:
        state:       Current sequence state.
        trigger:     Transition trigger string (see TRANSITION_TABLE keys).
        event:       Originating EngagementEvent, if the trigger came from an
                     inbound webhook.  Used for richer audit entries.
        audit_repo:  Used to write the audit log entry.

    Returns:
        Updated LeadSequenceState.

    Raises:
        ValueError: If the (from_status, trigger) pair is not in TRANSITION_TABLE.
    """
    from_status = state.status
    key = (from_status, trigger)

    if key not in TRANSITION_TABLE:
        raise ValueError(
            f"Invalid transition: status='{from_status.value}' trigger='{trigger}'.  "
            f"No entry in TRANSITION_TABLE for this combination."
        )

    to_status = TRANSITION_TABLE[key]
    logger.debug(
        "State transition: lead=%s state=%s %s → %s (trigger=%s)",
        state.lead_id,
        state.state_id,
        from_status.value,
        to_status.value,
        trigger,
    )

    state.status = to_status

    # Propagate stop reason from the event type if relevant.
    if to_status in (
        OrchestrationStatus.stopped_positive_reply,
        OrchestrationStatus.stopped_negative_reply,
        OrchestrationStatus.stopped_unsubscribed,
        OrchestrationStatus.stopped_bounced,
        OrchestrationStatus.stopped_disqualified,
    ):
        state.stop_reason = trigger
        if event is not None:
            state.stop_reason = event.event_type.value

    _write_audit(state, trigger, from_status, to_status, event, audit_repo)
    return state


# ---------------------------------------------------------------------------
# Event-driven state application
# ---------------------------------------------------------------------------


def apply_event_to_state(
    state: LeadSequenceState,
    event: EngagementEvent,
    audit_repo: AuditLogRepository,
) -> LeadSequenceState:
    """
    Apply an inbound engagement event to a sequence state.

    Checks stop conditions first.  If the event maps to a stop trigger the
    state is transitioned to the appropriate terminal status.  Non-stop events
    are recorded in the audit log but do not change the state status.

    Args:
        state:       Current sequence state.
        event:       The engagement event to apply.
        audit_repo:  Used to write audit log entries.

    Returns:
        Updated LeadSequenceState (may be unchanged if the event has no
        transition mapping).
    """
    if is_terminal(state.status):
        logger.debug(
            "apply_event_to_state: lead=%s is already terminal (%s)  -  ignoring event %s",
            state.lead_id,
            state.status.value,
            event.event_type.value,
        )
        return state

    trigger = STOP_EVENT_MAP.get(event.event_type)
    if trigger is None:
        # Non-stop event  -  log it but don't change state.
        logger.debug(
            "apply_event_to_state: event %s has no stop mapping for lead=%s (state=%s)",
            event.event_type.value,
            state.lead_id,
            state.status.value,
        )
        return state

    try:
        return transition_state(state, trigger, event, audit_repo)
    except ValueError:
        # Transition not valid from current status  -  log and continue.
        logger.warning(
            "apply_event_to_state: cannot apply trigger '%s' to status '%s' for lead=%s",
            trigger,
            state.status.value,
            state.lead_id,
        )
        return state
