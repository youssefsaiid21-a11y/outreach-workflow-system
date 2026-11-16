"""
Engagement scoring for the orchestration layer.

Scoring rules:
    1 email open:                         +1
    2+ email opens:                       +2  (replaces the +1)
    First email click:                    +3
    Any reply (positive or negative):     +5
    LinkedIn connect accepted:            +2
    No activity (score remains 0):        no penalty

The threshold for creating a manual task is stored in SequenceConfig
(default 5).  Use should_create_task_for_score() to check whether a task is
already pending before creating a duplicate.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .enums import EventType

if TYPE_CHECKING:
    from .repositories import EngagementEventRepository, ManualTaskRepository

logger = logging.getLogger(__name__)


def compute_score(
    lead_id: str,
    event_repo: EngagementEventRepository,
) -> int:
    """
    Calculate the current engagement score for a lead.

    The score is stateless and recomputed from events on every call.  This
    is cheap because the event table is indexed on lead_id and the counts are
    small (typically < 20 events per lead).

    Args:
        lead_id:    Internal lead identifier.
        event_repo: Repository used to query historical engagement events.

    Returns:
        Non-negative integer engagement score.
    """
    score = 0

    # ── Opens ────────────────────────────────────────────────────────────────
    open_count = event_repo.count_opens(lead_id)
    if open_count >= 2:
        score += 2
        logger.debug("scorer: lead=%s opens=%d +2", lead_id, open_count)
    elif open_count == 1:
        score += 1
        logger.debug("scorer: lead=%s opens=1 +1", lead_id)

    # ── Clicks ───────────────────────────────────────────────────────────────
    click_count = event_repo.count_distinct_click_sessions(lead_id)
    if click_count >= 1:
        score += 3
        logger.debug("scorer: lead=%s clicks=%d +3", lead_id, click_count)

    # ── Replies ──────────────────────────────────────────────────────────────
    reply_pos = event_repo.get_by_lead(lead_id, EventType.email_replied_positive)
    reply_neg = event_repo.get_by_lead(lead_id, EventType.email_replied_negative)
    if reply_pos or reply_neg:
        score += 5
        logger.debug("scorer: lead=%s replied +5", lead_id)

    # ── LinkedIn connection accepted ─────────────────────────────────────────
    li_events = event_repo.get_by_lead(lead_id, EventType.linkedin_connect_accepted)
    if li_events:
        score += 2
        logger.debug("scorer: lead=%s linkedin_connect_accepted +2", lead_id)

    logger.debug("scorer: lead=%s final_score=%d", lead_id, score)
    return score


def should_create_task_for_score(
    score: int,
    threshold: int,
    lead_id: str,
    task_repo: ManualTaskRepository,
) -> bool:
    """
    Determine whether a high-engagement manual task should be created.

    Returns True only if:
    1. score >= threshold, AND
    2. No open task already exists for this lead with "engagement" in its reason.

    This prevents duplicate tasks if the scheduler runs multiple times while
    the score stays above the threshold.

    Args:
        score:      Current engagement score.
        threshold:  Minimum score to trigger a task (from SequenceConfig).
        lead_id:    Internal lead identifier.
        task_repo:  Repository used to check for existing tasks.

    Returns:
        True if a new task should be created, False otherwise.
    """
    if score < threshold:
        return False

    existing_tasks = task_repo.get_by_lead(lead_id)
    for task in existing_tasks:
        if task.status in ("open", "in_progress") and "engagement" in (task.reason or "").lower():
            logger.debug(
                "should_create_task_for_score: lead=%s already has open engagement task %s",
                lead_id,
                task.task_id,
            )
            return False

    return True
