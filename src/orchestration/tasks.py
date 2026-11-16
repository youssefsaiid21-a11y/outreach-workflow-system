"""
Manual task factory helpers.

These functions create ManualTask records for specific situations that require
human review.  They encapsulate the priority logic and suggested action text
so callers don't repeat that logic.

Priority logic:
    score >= 8: urgent
    score >= 5: high
    else:       medium
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .enums import TaskPriority
from .models import LeadContext, ManualTask
from .repositories import ManualTaskRepository

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def _score_to_priority(score: int) -> TaskPriority:
    """
    Convert an engagement score to a TaskPriority.

    Args:
        score: Current engagement score for the lead.

    Returns:
        TaskPriority value.
    """
    if score >= 8:
        return TaskPriority.urgent
    if score >= 5:
        return TaskPriority.high
    return TaskPriority.medium


def create_task_for_high_score(
    lead: LeadContext,
    score: int,
    db: Session,
) -> ManualTask:
    """
    Create a manual task when a lead's engagement score crosses the threshold.

    This task prompts the sales rep to reach out personally to a lead who is
    showing high buying intent (multiple clicks, website visits, etc.) before
    the automated sequence finishes.

    Args:
        lead:  LeadContext snapshot for the lead.
        score: Current engagement score.
        db:    SQLAlchemy session.

    Returns:
        Persisted ManualTask Pydantic model.
    """
    priority = _score_to_priority(score)
    task = ManualTask(
        lead_id=lead.lead_id,
        reason=f"High engagement score ({score}) detected",
        priority=priority,
        suggested_action=(
            f"{lead.first_name} {lead.last_name} at {lead.company} has an engagement score of "
            f"{score}.  Consider a personal outreach: call, bespoke email, or LinkedIn DM. "
            f"Do NOT wait for the automated sequence to continue."
        ),
        due_date=_utcnow() + timedelta(hours=24),
    )

    repo = ManualTaskRepository(db)
    repo.save(task)
    logger.info(
        "create_task_for_high_score: task %s created for lead=%s score=%d priority=%s",
        task.task_id,
        lead.lead_id,
        score,
        priority.value,
    )
    return task


def create_task_for_positive_reply(
    lead: LeadContext,
    reply_id: str,
    db: Session,
) -> ManualTask:
    """
    Create a high-priority task when a prospect sends a positive reply.

    Args:
        lead:     LeadContext snapshot.
        reply_id: ID of the Reply record for reference.
        db:       SQLAlchemy session.

    Returns:
        Persisted ManualTask Pydantic model.
    """
    task = ManualTask(
        lead_id=lead.lead_id,
        reason="Positive reply received  -  follow up",
        priority=TaskPriority.high,
        suggested_action=(
            f"{lead.first_name} {lead.last_name} at {lead.company} replied positively. "
            f"Follow up within 24 hours  -  book a call or send a personal reply. "
            f"Reply ID: {reply_id}"
        ),
        due_date=_utcnow() + timedelta(hours=24),
    )

    repo = ManualTaskRepository(db)
    repo.save(task)
    logger.info(
        "create_task_for_positive_reply: task %s for lead=%s reply=%s",
        task.task_id,
        lead.lead_id,
        reply_id,
    )
    return task


def create_task_for_linkedin_not_accepted(
    lead: LeadContext,
    db: Session,
) -> ManualTask:
    """
    Create a manual task when a LinkedIn connection request was not accepted
    within the expected window and a LinkedIn message step is now due.

    The sales rep should manually send a personalised LinkedIn message or try
    a different channel.

    Args:
        lead: LeadContext snapshot for the lead.
        db:   SQLAlchemy session.

    Returns:
        Persisted ManualTask Pydantic model.
    """
    task = ManualTask(
        lead_id=lead.lead_id,
        reason="LinkedIn connection not accepted  -  message step due",
        priority=TaskPriority.medium,
        suggested_action=(
            f"LinkedIn connection to {lead.first_name} {lead.last_name} at {lead.company} "
            f"has not been accepted.  Send a manual LinkedIn InMail, or follow up via email "
            f"referencing the pending connection."
        ),
        due_date=_utcnow() + timedelta(days=2),
    )

    repo = ManualTaskRepository(db)
    repo.save(task)
    logger.info(
        "create_task_for_linkedin_not_accepted: task %s for lead=%s",
        task.task_id,
        lead.lead_id,
    )
    return task


def create_task_for_hard_bounce_key_account(
    lead: LeadContext,
    db: Session,
) -> ManualTask:
    """
    Create an urgent manual task when a hard bounce occurs for a key account.

    Key accounts warrant immediate human attention to verify the email address
    and decide whether to re-enrol with a corrected address or flag for the
    account team.

    Args:
        lead: LeadContext snapshot for the lead (must have is_key_account=True
              for this task to make sense, though the function doesn't enforce it).
        db:   SQLAlchemy session.

    Returns:
        Persisted ManualTask Pydantic model.
    """
    task = ManualTask(
        lead_id=lead.lead_id,
        reason="Hard email bounce on key account",
        priority=TaskPriority.urgent,
        suggested_action=(
            "Verify email manually and re-enroll or flag for account team. "
            f"Lead: {lead.first_name} {lead.last_name} | Company: {lead.company} | "
            f"Email: {lead.email} | Title: {lead.title}."
        ),
        due_date=_utcnow() + timedelta(hours=4),
    )

    repo = ManualTaskRepository(db)
    repo.save(task)
    logger.info(
        "create_task_for_hard_bounce_key_account: urgent task %s for lead=%s",
        task.task_id,
        lead.lead_id,
    )
    return task


def create_manual_override_task(
    lead: LeadContext,
    reason: str,
    db: Session,
) -> ManualTask:
    """
    Create a generic manual override task with a custom reason.

    Used when the orchestration layer encounters a situation that doesn't fit
    any specific factory function.

    Args:
        lead:   LeadContext snapshot for the lead.
        reason: Human-readable reason for creating the task.
        db:     SQLAlchemy session.

    Returns:
        Persisted ManualTask Pydantic model.
    """
    task = ManualTask(
        lead_id=lead.lead_id,
        reason=reason,
        priority=TaskPriority.medium,
        suggested_action=(
            f"Manual review required for {lead.first_name} {lead.last_name} at {lead.company}. "
            f"Reason: {reason}."
        ),
        due_date=_utcnow() + timedelta(days=1),
    )

    repo = ManualTaskRepository(db)
    repo.save(task)
    logger.info(
        "create_manual_override_task: task %s for lead=%s reason='%s'",
        task.task_id,
        lead.lead_id,
        reason,
    )
    return task
