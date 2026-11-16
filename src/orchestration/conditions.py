"""
Step condition evaluation for the orchestration layer.

Each sequence step can declare zero or more StepCondition objects.  All
conditions must pass (AND logic) for the step to execute.  If any condition
fails, the step is skipped and the reason is logged / returned to the caller.

Condition types handled:
    no_reply               -  No positive or negative reply received yet.
    no_unsubscribe         -  Lead has not unsubscribed.
    linkedin_url_exists    -  Lead has a LinkedIn URL in their profile.
    linkedin_connected     -  Lead has accepted our connection request.
    already_connected      -  Alias for linkedin_connected (idempotency guard).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .enums import EventType

if TYPE_CHECKING:
    from .models import LeadContext, LeadSequenceState, SequenceStep
    from .repositories import EngagementEventRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual condition evaluators
# ---------------------------------------------------------------------------


def _check_no_reply(
    lead_id: str,
    event_repo: EngagementEventRepository,
) -> tuple[bool, str]:
    """
    Return (True, reason) if the lead has not replied to any email.

    Args:
        lead_id:    Internal lead identifier.
        event_repo: Repository used to query engagement events.

    Returns:
        (True, "no_reply_ok") if no reply events exist.
        (False, "reply_already_received") if a positive or negative reply exists.
    """
    positive = event_repo.get_by_lead(lead_id, EventType.email_replied_positive)
    negative = event_repo.get_by_lead(lead_id, EventType.email_replied_negative)
    if positive or negative:
        return False, "reply_already_received"
    return True, "no_reply_ok"


def _check_no_unsubscribe(
    lead_id: str,
    event_repo: EngagementEventRepository,
) -> tuple[bool, str]:
    """
    Return (True, reason) if the lead has not unsubscribed.

    Args:
        lead_id:    Internal lead identifier.
        event_repo: Repository used to query engagement events.

    Returns:
        (True, "not_unsubscribed") if no unsubscribe event exists.
        (False, "lead_unsubscribed") if an unsubscribe event is found.
    """
    events = event_repo.get_by_lead(lead_id, EventType.unsubscribed)
    if events:
        return False, "lead_unsubscribed"
    return True, "not_unsubscribed"


def _check_linkedin_url_exists(lead: LeadContext) -> tuple[bool, str]:
    """
    Return (True, reason) if the lead has a LinkedIn URL.

    Args:
        lead: LeadContext snapshot.

    Returns:
        (True, "linkedin_url_present") or (False, "no_linkedin_url").
    """
    if lead.linkedin_url and lead.linkedin_url.strip():
        return True, "linkedin_url_present"
    return False, "no_linkedin_url"


def _check_linkedin_connected(
    lead: LeadContext,
    event_repo: EngagementEventRepository,
) -> tuple[bool, str]:
    """
    Return (True, reason) if the lead is a 1st-degree LinkedIn connection.

    The check accepts either:
    - lead.linkedin_connected == True (set by the LinkedIn provider callback), OR
    - An existing linkedin_connect_accepted event in the DB.

    Args:
        lead:       LeadContext snapshot.
        event_repo: Repository used to query engagement events.

    Returns:
        (True, "linkedin_connected") or (False, "not_linkedin_connected").
    """
    if lead.linkedin_connected:
        return True, "linkedin_connected_via_flag"

    events = event_repo.get_by_lead(lead.lead_id, EventType.linkedin_connect_accepted)
    if events:
        return True, "linkedin_connected_via_event"

    return False, "not_linkedin_connected"


# ---------------------------------------------------------------------------
# Public evaluation function
# ---------------------------------------------------------------------------


def evaluate_conditions(
    step: SequenceStep,
    lead: LeadContext,
    state: LeadSequenceState,
    event_repo: EngagementEventRepository,
) -> tuple[bool, str]:
    """
    Evaluate all conditions on a step and return whether it should execute.

    All conditions must pass (AND logic).  Returns on the first failing
    condition so the caller knows exactly which condition blocked execution.

    Args:
        step:       The sequence step whose conditions are being evaluated.
        lead:       Current lead context snapshot.
        state:      Current sequence state (included for future condition types
                    that may depend on the state, e.g. step count).
        event_repo: Repository for querying historical engagement events.

    Returns:
        (True, "no_conditions") if there are no conditions to evaluate.
        (True, "<condition_type>_ok") if all conditions pass.
        (False, "<condition_type>:<reason>") for the first failing condition.
    """
    if not step.conditions:
        return True, "no_conditions"

    for condition in step.conditions:
        ctype = condition.condition_type

        if ctype == "no_reply":
            passed, reason = _check_no_reply(lead.lead_id, event_repo)
        elif ctype == "no_unsubscribe":
            passed, reason = _check_no_unsubscribe(lead.lead_id, event_repo)
        elif ctype == "linkedin_url_exists":
            passed, reason = _check_linkedin_url_exists(lead)
        elif ctype in ("linkedin_connected", "already_connected"):
            passed, reason = _check_linkedin_connected(lead, event_repo)
        else:
            # Fix 19: Unknown condition type fails-closed (not fails-open)
            logger.error(
                "evaluate_conditions: unknown condition type '%s' on step %s  -  treating as failed (fail-closed)",
                ctype,
                step.step_id,
            )
            return False, f"unknown_condition_type:{ctype}"

        if not passed:
            logger.debug(
                "Condition '%s' failed for lead=%s step=%s: %s",
                ctype,
                lead.lead_id,
                step.step_id,
                reason,
            )
            return False, f"{ctype}:{reason}"

    return True, "all_conditions_passed"


def step_should_execute(
    step: SequenceStep,
    lead: LeadContext,
    state: LeadSequenceState,
    event_repo: EngagementEventRepository,
) -> tuple[bool, str]:
    """
    Determine whether a sequence step should execute now.

    Checks:
    1. step.enabled  -  disabled steps are always skipped.
    2. All declared conditions via evaluate_conditions().

    Args:
        step:       The sequence step to evaluate.
        lead:       Current lead context snapshot.
        state:      Current sequence state.
        event_repo: Repository for querying historical engagement events.

    Returns:
        (True, reason)  if the step should execute.
        (False, reason) if the step should be skipped, with the reason why.
    """
    if not step.enabled:
        return False, "step_disabled"

    return evaluate_conditions(step, lead, state, event_repo)
