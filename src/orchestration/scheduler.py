"""
Scheduler  -  the heartbeat of the orchestration engine.

The scheduler runs periodically (every 5 minutes via the background loop in
main.py) and drives the entire outreach sequence forward. On each tick it:

  1. Queries orch_sequence_states for all leads whose next step is due
     (status in active/waiting_for_next_step AND scheduled_next_at <= now)
  2. For each due state, loads the sequence config and the prospect data
  3. Checks daily send limits (email) and daily connect caps (LinkedIn)
  4. Evaluates step conditions (no_reply, linkedin_connected, etc.)
  5. Dispatches the step to the appropriate handler via ActionDispatcher
  6. Advances the state to the next step and computes the next scheduled_at

The scheduler is intentionally stateless: it reads from the DB, processes
each due state, and writes results back. It can be called from:
  - The background scheduler loop in main.py (production)
  - The POST /scheduler/run admin endpoint (manual trigger)
  - The CLI (testing)
  - Unit tests (with a fake clock)

KEY DESIGN DECISIONS:
  - Timing anchored to enrolled_at + day_offset (not execution time) to prevent drift
  - Idempotency: deterministic execution_ids mean duplicate processing is safe
  - Per-user scoping: the background loop calls run_scheduler once per active user
    so each tick uses that user's PhantomBuster credentials
  - Pessimistic locking on Postgres (SELECT FOR UPDATE SKIP LOCKED) prevents
    two concurrent scheduler runs from double-processing the same lead
  - LinkedIn connect cap: daily limit per user, deferred to next working day
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .conditions import step_should_execute
from .enums import OrchestrationStatus, StepResultStatus, StepType
from .models import ActionExecution, LeadSequenceState, SchedulerRunSummary, SequenceConfig
from .orm import ActionExecutionORM
from .repositories import (
    ActionExecutionRepository,
    AuditLogRepository,
    LeadSequenceStateRepository,
    ProspectRepository,
    _orm_to_state,
    build_execution_id,
)
from .state_machine import is_terminal, transition_state

if TYPE_CHECKING:
    from .dispatcher import ActionDispatcher

logger = logging.getLogger(__name__)

# ── Safety constants ────────────────────────────────────────────────────────
# P1-7 / deep audit LOW-1: these used to be hardcoded literals here. They
# now live in src/config.py and are env-var overridable. Re-exported under
# the original names so test_stress_day10 (and any external importer) keeps
# working without churn.
from ..config import (  # noqa: E402
    SCHEDULER_BATCH_LIMIT as SCHEDULER_BATCH_LIMIT,
    DEFAULT_DAILY_SEND_LIMIT as DEFAULT_DAILY_SEND_LIMIT,
    DEFAULT_LINKEDIN_CONNECT_DAILY_CAP as DEFAULT_LINKEDIN_CONNECT_DAILY_CAP,
)


def get_due_steps(
    now: datetime,
    db: Session,
    user_id: str | None = None,
) -> list[LeadSequenceState]:
    """
    Return all sequence states where the next step is due for execution.

    A state is considered due when:
    - status is active or waiting_for_next_step
    - scheduled_next_at is not None and <= now
    - P3-4: (optional) its ``user_id`` matches the filter

    Args:
        now:     Current UTC datetime used as the cutoff.
        db:      SQLAlchemy session.
        user_id: Optional  -  only return states owned by this user.

    Returns:
        List of LeadSequenceState Pydantic models.
    """
    repo = LeadSequenceStateRepository(db)
    rows = repo.get_due(now, limit=SCHEDULER_BATCH_LIMIT, user_id=user_id)
    states = [_orm_to_state(row) for row in rows]
    logger.debug(
        "get_due_steps: found %d due states at %s (batch_limit=%d user=%s)",
        len(states), now.isoformat(), SCHEDULER_BATCH_LIMIT, user_id or "<all>",
    )
    return states


def _count_todays_linkedin_connects(
    db: Session,
    user_id: str | None,
    now: datetime,
) -> int:
    """
    Return the number of LinkedIn connect slots already consumed today
    by this user, counting across every campaign they own.

    "Consumed" = rows in ``linkedin_queue`` with
    ``action_type='connect'``, ``user_id`` matching, created today
    (``created_at >= today_start``), and status in
    ``(queued, processing, done)``. Failed rows do NOT count  -  those
    represent attempts that never actually used a LinkedIn invite slot,
    so the cap should not hold them against the user. Skipped rows do
    not count for the same reason.

    LinkedIn's own cap is weekly (~100/week) but the Outbound Agent
    Phantom enforces a per-day ceiling (~20 by default), and the
    practical concern for us is "don't queue a 21st profile today or
    PhantomBuster will quietly skip it". Using UTC day boundaries here
    matches PhantomBuster's internal reset clock closely enough  -  off
    by at most the worker's own timezone, which is acceptable for v1.

    Returns 0 when ``user_id`` is None (legacy path has no user scope;
    the cap is per-user-LinkedIn-account anyway so with no user it's
    meaningless to enforce).
    """
    from ..db.models import LinkedInQueue, LinkedInQueueStatus

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    q = (
        db.query(func.count(LinkedInQueue.id))
        .filter(
            LinkedInQueue.action_type == "connect",
            LinkedInQueue.created_at >= today_start,
            LinkedInQueue.status.in_([
                LinkedInQueueStatus.queued,
                LinkedInQueueStatus.processing,
                LinkedInQueueStatus.done,
            ]),
        )
    )
    # Bug #18 fix: when user_id is None, count ALL rows (not 0).
    # The cap should still be enforced even in legacy/single-user mode.
    if user_id:
        q = q.filter(LinkedInQueue.user_id == user_id)
    count = q.scalar()
    return int(count or 0)


def _resolve_user_linkedin_daily_cap(db: Session, user_id: str | None) -> int:
    """
    Per-user LinkedIn connect daily cap with the same resolution pattern
    as ``_resolve_user_daily_send_limit``:

    1. ``app_settings`` row ``__user__{uid}__linkedin_connect_daily_cap``
       (lets a rep lower their cap for a sub-100 weekly run-rate).
    2. ``DEFAULT_LINKEDIN_CONNECT_DAILY_CAP`` from config
       (env-overridable via ``LINKEDIN_CONNECT_DAILY_CAP``).
    """
    from ..db.models import AppSetting

    if user_id:
        try:
            row = (
                db.query(AppSetting)
                .filter_by(key=f"__user__{user_id}__linkedin_connect_daily_cap")
                .first()
            )
            if row and row.value is not None:
                try:
                    val = int(str(row.value).strip())
                    if val >= 0:
                        # Accept 0 explicitly  -  "disable connects for
                        # this user today" is a legitimate override,
                        # not a "value not set" signal.
                        return val
                except ValueError:
                    logger.warning(
                        "Invalid per-user linkedin_connect_daily_cap for user=%s: %r",
                        user_id, row.value,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not read per-user linkedin_connect_daily_cap for %s: %s",
                user_id, exc,
            )
    return DEFAULT_LINKEDIN_CONNECT_DAILY_CAP


def _next_working_day_start(now: datetime) -> datetime:
    """
    Return the next calendar day at 00:05 UTC, skipping Saturday and
    Sunday. Used to reschedule LinkedIn-connect states that are over
    their daily cap so we don't re-query them every 5 minutes for the
    rest of today.

    00:05 (rather than 00:00) is deliberate: it gives the next-day
    scheduler tick an obvious rendezvous time without racing any
    UTC-midnight rollover logic. Weekends are skipped so Friday-over-cap
    deferrals don't land on Saturday and immediately get deferred again
    by the Phantom's weekdays-only window.
    """
    # Start from today's 00:05 UTC then advance by one day at a time
    # until we land on a weekday (Mon=0 … Fri=4).
    candidate = now.replace(hour=0, minute=5, second=0, microsecond=0) + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _count_todays_sends(db: Session, sequence_id: str, now: datetime) -> int:
    """
    Count the number of successful send_email executions recorded today (UTC)
    for a given sequence_id.

    Uses the ActionExecutionORM table joined back to OrchSequenceStateORM to
    scope the count to a specific sequence.

    Args:
        db:          SQLAlchemy session.
        sequence_id: The orchestration sequence identifier.
        now:         Current UTC datetime (used to derive today's date boundary).

    Returns:
        Integer count of today's successful send_email executions.
    """
    from .orm import OrchSequenceStateORM

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    count = (
        db.query(func.count(ActionExecutionORM.execution_id))
        .join(
            OrchSequenceStateORM,
            ActionExecutionORM.state_id == OrchSequenceStateORM.state_id,
        )
        .filter(
            OrchSequenceStateORM.sequence_id == sequence_id,
            ActionExecutionORM.action_type == StepType.send_email.value,
            ActionExecutionORM.status == StepResultStatus.executed.value,
            ActionExecutionORM.attempted_at >= today_start,
        )
        .scalar()
    )
    return count or 0


def compute_next_scheduled_at(
    state: LeadSequenceState,
    config: SequenceConfig,
    now: datetime,
) -> datetime | None:
    """
    Calculate when the next step should run after the current step is complete.

    Timing is anchored to enrolled_at + next_step.day_offset to prevent drift
    caused by slow execution or delayed scheduler runs.

    Args:
        state:  Current sequence state (current_step_index already incremented
                to point at the NEXT step).
        config: Sequence configuration.
        now:    Current UTC datetime (used only as a fallback if enrolled_at
                is somehow in the future).

    Returns:
        UTC datetime for the next step, or None if no more steps remain.
    """
    next_index = state.current_step_index
    if next_index >= len(config.steps):
        logger.debug(
            "compute_next_scheduled_at: no more steps for state=%s (index=%d, total=%d)",
            state.state_id,
            next_index,
            len(config.steps),
        )
        return None

    next_step = config.steps[next_index]
    # Anchor to enrolled_at so timing is deterministic regardless of when the
    # scheduler actually runs.
    enrolled_at = state.enrolled_at
    if enrolled_at.tzinfo is None:
        enrolled_at = enrolled_at.replace(tzinfo=timezone.utc)

    scheduled_dt = enrolled_at + timedelta(days=next_step.day_offset)

    # Fix 11: advance to Monday if scheduled on a weekend and business_days_only is set
    if config.business_days_only:
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(config.timezone)
        except Exception:
            tz = ZoneInfo("UTC")
        local_dt = scheduled_dt.astimezone(tz)
        weekday = local_dt.weekday()  # 0=Mon, 6=Sun
        if weekday == 5:  # Saturday → push to Monday
            local_dt = local_dt + timedelta(days=2)
        elif weekday == 6:  # Sunday → push to Monday
            local_dt = local_dt + timedelta(days=1)
        scheduled_dt = local_dt.astimezone(timezone.utc)

    logger.debug(
        "compute_next_scheduled_at: state=%s next_step=%s day_offset=%d → %s",
        state.state_id,
        next_step.step_id,
        next_step.day_offset,
        scheduled_dt.isoformat(),
    )
    return scheduled_dt


def execute_next_step(
    state: LeadSequenceState,
    config: SequenceConfig,
    lead: Any,  # LeadContext
    dispatcher: ActionDispatcher,
    db: Session,
) -> StepResult:
    """
    Execute the next due step for a lead sequence state.

    Called once per due state from `run_scheduler`. The "fast path" for
    advancing the next pending step:

    1. Validate step index is within bounds.
    2. Compute attempt = count_attempts() + 1 and build the execution_id
       via `build_execution_id()`  -  the SAME helper used by
       `dispatcher.dispatch`, so both call sites cannot disagree (P2-7 /
       deep audit HIGH-6).
    3. If that execution_id already exists (idempotency check, P2-7 now
       INCLUDES failed rows), advance the state and return skipped.
    4. Otherwise evaluate step conditions, dispatch to the handler, advance.

    Args:
        state:      Current LeadSequenceState.
        config:     SequenceConfig the state belongs to.
        lead:       LeadContext for the lead.
        dispatcher: ActionDispatcher instance.
        db:         SQLAlchemy session.

    Returns:
        StepResult from the handler.
    """
    from .models import StepResult
    from datetime import datetime

    action_repo = ActionExecutionRepository(db)
    audit_repo = AuditLogRepository(db)
    state_repo = LeadSequenceStateRepository(db)

    # Bounds check
    if state.current_step_index >= len(config.steps):
        logger.warning(
            "execute_next_step: state=%s step_index=%d out of bounds (total=%d)",
            state.state_id,
            state.current_step_index,
            len(config.steps),
        )
        return StepResult(
            step_id="unknown",
            status=StepResultStatus.skipped,
            detail="step_index out of bounds",
        )

    # Fix 2: Transition waiting_for_next_step → active before executing
    if state.status == OrchestrationStatus.waiting_for_next_step:
        state = transition_state(state, "step_due", None, audit_repo)
        state_repo.upsert_from_pydantic(state)
        db.flush()

    current_step = config.steps[state.current_step_index]

    # P2-7: single source of truth for execution_id construction. Always
    # increment from total prior attempts (including failed) so retries
    # cannot collide with the failed row's id.
    attempt = action_repo.count_attempts(state.state_id, current_step.step_id) + 1
    execution_id = build_execution_id(state.state_id, current_step.step_id, attempt)

    # Idempotency check (Fix 3: only blocks on non-failed records)
    if action_repo.exists(execution_id):
        logger.info(
            "execute_next_step: execution_id=%s already exists  -  skipping (idempotent)",
            execution_id,
        )
        # Advance state as if it executed so the scheduler doesn't loop.
        _advance_and_save(state, config, audit_repo, state_repo, db)
        return StepResult(
            step_id=current_step.step_id,
            status=StepResultStatus.skipped,
            execution_id=execution_id,
            detail="idempotent_skip",
        )

    # Evaluate step conditions (no_reply, linkedin_connected, linkedin_url_exists, etc.)
    # before dispatching. Conditions are defined per-step in the SequenceConfig.
    from .repositories import EngagementEventRepository
    event_repo = EngagementEventRepository(db)
    should_run, skip_reason = step_should_execute(current_step, lead, state, event_repo)
    if not should_run:
        # TEMPORAL vs PERMANENT conditions:
        # "linkedin_connected" is temporal — the prospect might accept the connection
        # request later, so we defer (reschedule in 6h) instead of permanently skipping.
        # Other conditions (no_reply, no_unsubscribe) are permanent — if violated,
        # the step is skipped and the sequence advances to the next step.
        # Match both raw ("not_linkedin_connected") and prefixed
        # ("linkedin_connected:not_linkedin_connected") formats from evaluate_conditions.
        temporal_conditions = {"not_linkedin_connected", "linkedin_connected:not_linkedin_connected"}
        if skip_reason in temporal_conditions:
            # Defer: reschedule for 6 hours from now so we re-check
            # after the acceptance checker has had time to detect it.
            # Max deferral: 30 days from enrollment. After that, give up
            # and advance past the step (the handler's fallback creates
            # a manual task for the sales rep).
            max_deferral_days = 30
            enrolled = state.enrolled_at
            if enrolled and enrolled.tzinfo is None:
                enrolled = enrolled.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - enrolled).days if enrolled else max_deferral_days + 1
            if age_days > max_deferral_days:
                logger.warning(
                    "Step %s deferral exceeded %d days (age=%dd) — skipping permanently: lead=%s",
                    current_step.step_id, max_deferral_days, age_days, state.lead_id,
                )
                # Fall through to the permanent skip path below
            else:
                state.scheduled_next_at = datetime.now(timezone.utc) + timedelta(hours=6)
                state.status = OrchestrationStatus.waiting_for_next_step
                state_repo.upsert_from_pydantic(state)
                db.flush()
                logger.info(
                    "Step %s deferred (condition %s not met yet, will retry in 6h, age=%dd/%dd): lead=%s",
                    current_step.step_id, skip_reason, age_days, max_deferral_days, state.lead_id,
                )
                return StepResult(step_id=current_step.step_id, status=StepResultStatus.skipped, detail=f"deferred:{skip_reason}")

        # Permanent skip - advance past the step
        logger.info("Step %s skipped: %s", current_step.step_id, skip_reason)
        skipped_exec = ActionExecution(
            execution_id=execution_id,
            lead_id=state.lead_id,
            step_id=current_step.step_id,
            action_type=current_step.step_type,
            status=StepResultStatus.skipped,
            attempted_at=datetime.now(timezone.utc),
            result_payload={"skip_reason": skip_reason},
            error_message=None,
            state_id=state.state_id,
        )
        action_repo.save(skipped_exec)
        db.flush()
        _advance_and_save(state, config, audit_repo, state_repo, db, result_status=StepResultStatus.skipped)
        return StepResult(step_id=current_step.step_id, status=StepResultStatus.skipped, detail=skip_reason)

    # Dispatch
    result = dispatcher.dispatch(step=current_step, lead=lead, state=state)

    # P2-2 / HIGH-4: do NOT advance the step pointer on failure. The
    # dispatcher's retry ladder reschedules the same step for a future
    # tick (or pauses after MAX_CONSECUTIVE_FAILURES). Advancing here
    # would skip the failing step and immediately attempt the next one.
    if result.status != StepResultStatus.failed:
        _advance_and_save(state, config, audit_repo, state_repo, db, result_status=result.status)

    return result


def _advance_and_save(
    state: LeadSequenceState,
    config: SequenceConfig,
    audit_repo: Any,
    state_repo: LeadSequenceStateRepository,
    db: Session,
    result_status: StepResultStatus = StepResultStatus.executed,
) -> None:
    """
    Increment the step index, compute next scheduled_at, and transition FSM.

    Args:
        state:         Current LeadSequenceState (mutated in place).
        config:        SequenceConfig.
        audit_repo:    AuditLogRepository.
        state_repo:    LeadSequenceStateRepository.
        db:            SQLAlchemy session.
        result_status: Status of the step execution (Fix 6/14  -  only add to
                       completed_steps on actual execution).
    """
    # Audit 2026-05-05 #16: bounds check before accessing config.steps.
    # A bug in the dispatcher or a double-advance could push step_index
    # past the end of the sequence, causing an IndexError crash.
    if state.current_step_index >= len(config.steps):
        logger.error(
            "advance_and_save: step_index %d >= len(steps) %d for state %s  -  "
            "refusing to advance further (sequence may already be complete)",
            state.current_step_index, len(config.steps), state.state_id,
        )
        return

    # Mark current step as completed only on successful execution (Fix 6/14)
    step_id = config.steps[state.current_step_index].step_id
    if result_status == StepResultStatus.executed:
        updated_completed = list(state.completed_steps) + [step_id]
    else:
        updated_completed = list(state.completed_steps)
    state.completed_steps = updated_completed

    # Advance index
    state.current_step_index += 1

    # Compute next schedule
    now = datetime.now(timezone.utc)
    next_at = compute_next_scheduled_at(state, config, now)
    state.scheduled_next_at = next_at

    # FSM transition: if not already terminal, move to waiting_for_next_step
    if not is_terminal(state.status):
        if next_at is None:
            # No more steps  -  the end_sequence handler should have already fired.
            # Transition to completed as a safety net.
            try:
                transition_state(state, "sequence_ended", None, audit_repo)
            except ValueError:
                pass  # Already in a terminal state
        elif state.status == OrchestrationStatus.active:
            try:
                transition_state(state, "step_executed", None, audit_repo)
            except ValueError:
                pass
        # If status is waiting_for_next_step we leave it  -  the next scheduler run
        # will move it back to active when the time comes.

    state_repo.upsert_from_pydantic(state)


def _upsert_scheduler_last_run(db: Session) -> None:
    """
    P2-6 / deep audit MEDIUM-10: write `scheduler_last_run` atomically.

    Postgres path uses SQLAlchemy's `Insert.on_conflict_do_update()` so
    two concurrent scheduler ticks cannot both insert / both update with
    different timestamps in a torn read-then-update window.

    SQLite path uses `INSERT OR REPLACE`, which is atomic at the engine
    level for PK conflicts.

    Failures are logged at WARNING level (this is bookkeeping  -  never
    let it crash a successful scheduler tick).
    """
    from ..db.models import AppSetting

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        dialect_name = db.bind.dialect.name if db.bind is not None else ""
    except Exception:
        dialect_name = ""

    try:
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(AppSetting.__table__).values(
                key="scheduler_last_run",
                value=now_iso,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[AppSetting.__table__.c.key],
                set_={"value": stmt.excluded.value},
            )
            db.execute(stmt)
            db.commit()
            return

        if dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(AppSetting.__table__).values(
                key="scheduler_last_run",
                value=now_iso,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[AppSetting.__table__.c.key],
                set_={"value": stmt.excluded.value},
            )
            db.execute(stmt)
            db.commit()
            return

        # Fallback for unknown dialects: best-effort read-update.
        setting = db.query(AppSetting).filter_by(key="scheduler_last_run").first()
        if setting:
            setting.value = now_iso
        else:
            db.add(AppSetting(key="scheduler_last_run", value=now_iso))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_upsert_scheduler_last_run: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


def _resolve_user_daily_send_limit(
    db: Session,
    user_id: str | None,
    sequence_daily_limit: int | None,
) -> int:
    """
    P3-4: per-user daily-send ceiling.

    Resolution order:
      1. If a user is scoped and an ``__user__{uid}__daily_send_limit``
         row exists in ``app_settings``, that wins.
      2. Else the sequence-level ``daily_send_limit`` from the
         SequenceConfig (if any).
      3. Else the global ``DEFAULT_DAILY_SEND_LIMIT`` from src/config.py.
    """
    from ..db.models import AppSetting

    if user_id:
        try:
            row = (
                db.query(AppSetting)
                .filter_by(key=f"__user__{user_id}__daily_send_limit")
                .first()
            )
            if row and row.value:
                try:
                    val = int(str(row.value).strip())
                    if val > 0:
                        return val
                except ValueError:
                    logger.warning(
                        "Invalid per-user daily_send_limit for user=%s: %r (ignoring)",
                        user_id, row.value,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read per-user daily_send_limit for %s: %s", user_id, exc)

    return sequence_daily_limit or DEFAULT_DAILY_SEND_LIMIT


def _fetch_due_states_and_prospects(
    now: datetime,
    db: Session,
    user_id: str | None,
) -> tuple[list[LeadSequenceState], dict]:
    """Fetch due sequence states and batch-load their prospects.

    Returns (due_states, prospects_map) where prospects_map is
    {prospect_id: Prospect ORM object} for all leads in due_states.
    Avoids N+1 queries by batch-fetching all prospects upfront.
    """
    from ..db.models import Prospect

    due_states = get_due_steps(now, db, user_id=user_id)
    lead_ids = [s.lead_id for s in due_states]
    prospects_map = (
        {p.id: p for p in db.query(Prospect).filter(Prospect.id.in_(lead_ids)).all()}
        if lead_ids else {}
    )
    return due_states, prospects_map


def _init_daily_caches() -> tuple[dict[str, int], dict[str, int], str]:
    """Initialise the per-tick daily send and LinkedIn connect caches.

    Returns (daily_send_counts, daily_linkedin_counts, linkedin_no_user_sentinel).
    Both dicts start empty and are populated lazily during the main loop.
    """
    _daily_send_counts: dict[str, int] = {}
    _daily_linkedin_counts: dict[str, int] = {}
    _LINKEDIN_NO_USER = "__no_user__"
    return _daily_send_counts, _daily_linkedin_counts, _LINKEDIN_NO_USER


def run_scheduler(
    now: datetime,
    db: Session,
    dispatcher: ActionDispatcher,
    user_id: str | None = None,
) -> SchedulerRunSummary:
    """
    Run one full scheduler cycle: find all due states and execute their next steps.

    Each state is processed in its own try/except block so a failure in one
    lead's step doesn't block processing of other leads.

    P3-4 / deep audit CRITICAL-3: ``user_id`` scopes the tick to one rep.
    The background scheduler loop iterates active users and calls this once
    per user per interval so each tick uses that user's PhantomBuster
    credentials via the per-user provider cache. Calling
    without ``user_id`` retains legacy "process everything" behaviour used
    by the ``/scheduler/run`` admin endpoint, the CLI, and the test suite.

    Args:
        now:        Current UTC datetime (passed explicitly for testability).
        db:         SQLAlchemy session.
        dispatcher: ActionDispatcher configured with the active providers.
        user_id:    Optional  -  only process this user's sequence states.

    Returns:
        SchedulerRunSummary with counts of outcomes.
    """
    from .config import get_sequence_config
    from .models import LeadContext

    summary = SchedulerRunSummary()

    # ── Phase 1: Fetch due states + pre-fetch prospects ──────────────────────
    due_states, prospects_map = _fetch_due_states_and_prospects(now, db, user_id)
    summary.states_processed = len(due_states)

    # ── Phase 2: Initialize daily caches ─────────────────────────────────────
    _daily_send_counts, _daily_linkedin_counts, _LINKEDIN_NO_USER = _init_daily_caches()

    # ── Phase 3: Main per-state processing loop ──────────────────────────────
    for state in due_states:
        try:
            config = get_sequence_config(state.sequence_id)
        except KeyError:
            # SWAT-M3: pause the state so it doesn't error-loop every tick
            msg = f"Unknown sequence_id '{state.sequence_id}' for state={state.state_id}"
            logger.error(msg)
            summary.errors.append(msg)
            from .orm import OrchSequenceStateORM
            orm_row = db.query(OrchSequenceStateORM).filter_by(state_id=state.state_id).first()
            if orm_row and orm_row.status not in ("completed", "stopped"):
                orm_row.status = "paused"
                orm_row.paused_reason = "sequence_config_not_found"
                db.commit()
                logger.warning("Paused state %s: sequence config '%s' not found", state.state_id, state.sequence_id)
            continue

        # Fix 13: Use pre-fetched prospect map
        prospect = prospects_map.get(state.lead_id)
        if prospect is None:
            msg = f"Prospect not found for lead_id={state.lead_id} state={state.state_id}"
            logger.error(msg)
            summary.errors.append(msg)
            continue

        lead = LeadContext(
            lead_id=prospect.id,
            email=prospect.email,
            first_name=prospect.first_name,
            last_name=prospect.last_name or "",
            company=prospect.company or "",
            title=prospect.title or "",
            industry=prospect.industry or "",
            location=prospect.location or "",
            linkedin_url=prospect.linkedin_url,
            linkedin_connected=getattr(prospect, "linkedin_connected", False) or False,
            linkedin_connect_sent_at=getattr(prospect, "linkedin_connect_sent_at", None),
            tags=getattr(prospect, "tags", None) or [],
        )

        # ── Daily send limit check ──────────────────────────────────────
        # If the next step is a send_email, verify we haven't exceeded today's
        # sending limit for this sequence.  If the limit is reached, leave the
        # state as-is so the next scheduler run (tomorrow) picks it up.
        current_step_idx = state.current_step_index
        is_send_email_step = False
        is_send_linkedin_connect_step = False
        if current_step_idx < len(config.steps):
            next_step = config.steps[current_step_idx]
            is_send_email_step = next_step.step_type == StepType.send_email
            is_send_linkedin_connect_step = next_step.step_type == StepType.send_linkedin_connect
            if is_send_email_step:
                seq_id = state.sequence_id
                if seq_id not in _daily_send_counts:
                    _daily_send_counts[seq_id] = _count_todays_sends(db, seq_id, now)
                # P3-4: per-user override via app_settings, else sequence
                # limit, else global DEFAULT_DAILY_SEND_LIMIT.
                daily_limit = _resolve_user_daily_send_limit(
                    db, user_id, getattr(config, "daily_send_limit", None),
                )
                if _daily_send_counts[seq_id] >= daily_limit:
                    logger.info(
                        "Daily send limit reached for sequence=%s (%d/%d)  -  skipping state=%s",
                        seq_id, _daily_send_counts[seq_id], daily_limit, state.state_id,
                    )
                    summary.steps_skipped += 1
                    continue

            # 2026-04-10: LinkedIn connect daily cap (mirrors email path
            # but reschedules the state so we don't re-evaluate every
            # 5-minute tick for the rest of today).
            if is_send_linkedin_connect_step:
                # Resolve the owning user. `user_id` from run_scheduler's
                # outer scope is set in the per-user loop; if None (legacy
                # path), fall back to the state's stored user_id via the
                # prospect or the campaign. For the normal per-user
                # scheduler loop, `user_id` is always set.
                effective_user = user_id
                if not effective_user:
                    effective_user = getattr(prospect, "user_id", None)
                cache_key = effective_user or _LINKEDIN_NO_USER

                if cache_key not in _daily_linkedin_counts:
                    _daily_linkedin_counts[cache_key] = _count_todays_linkedin_connects(
                        db, effective_user, now,
                    )
                li_cap = _resolve_user_linkedin_daily_cap(db, effective_user)

                if _daily_linkedin_counts[cache_key] >= li_cap:
                    # Over the cap for today. Reschedule to the next
                    # working day at 00:05 UTC so we don't re-query
                    # this state every 5 minutes until midnight.
                    next_working_start = _next_working_day_start(now)
                    try:
                        state.scheduled_next_at = next_working_start
                        state_repo = LeadSequenceStateRepository(db)
                        state_repo.upsert_from_pydantic(state)
                        logger.info(
                            "LinkedIn daily cap reached for user=%s (%d/%d)  -  "
                            "deferring state=%s (lead=%s) to %s",
                            effective_user or "<none>",
                            _daily_linkedin_counts[cache_key],
                            li_cap,
                            state.state_id,
                            state.lead_id,
                            next_working_start.isoformat(),
                        )
                    except Exception as defer_err:  # noqa: BLE001
                        logger.warning(
                            "LinkedIn defer reschedule failed for state=%s: %s",
                            state.state_id, defer_err,
                        )
                    summary.steps_deferred += 1
                    continue

        try:
            result = execute_next_step(
                state=state,
                config=config,
                lead=lead,
                dispatcher=dispatcher,
                db=db,
            )
            if result.status == StepResultStatus.executed:
                summary.steps_executed += 1
                # Track send_email executions for daily limit enforcement
                if is_send_email_step:
                    _daily_send_counts[state.sequence_id] = _daily_send_counts.get(state.sequence_id, 0) + 1
                # Track send_linkedin_connect executions for LinkedIn
                # cap enforcement. Increment the per-user cache so the
                # next over-cap state in this same tick gets deferred
                # immediately, without a round-trip to count again.
                if is_send_linkedin_connect_step:
                    effective_user = user_id or getattr(prospect, "user_id", None)
                    cache_key = effective_user or _LINKEDIN_NO_USER
                    _daily_linkedin_counts[cache_key] = _daily_linkedin_counts.get(cache_key, 0) + 1
            elif result.status == StepResultStatus.skipped:
                summary.steps_skipped += 1
            elif result.status == StepResultStatus.failed:
                summary.steps_failed += 1
                summary.errors.append(
                    f"state={state.state_id} step={result.step_id}: {result.detail}"
                )
            elif result.status == StepResultStatus.deferred:
                summary.steps_deferred += 1  # Fix 22
        except StaleDataError:
            # Audit 2026-05-05 #6: escalate from INFO to WARNING. A single
            # collision is fine (webhook ingestion won the race), but
            # repeated collisions for the same state_id mean it's stuck.
            logger.warning(
                "Orch state %s was modified concurrently - skipping (another writer advanced)",
                state.state_id,
            )
            summary.steps_skipped += 1
            continue
        except Exception as exc:  # noqa: BLE001
            msg = f"Unhandled error processing state={state.state_id}: {exc}"
            logger.exception(msg)
            summary.errors.append(msg)
            summary.steps_failed += 1

    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("run_scheduler: db.commit() failed: %s", exc)
        db.rollback()
        summary.errors.append(f"DB commit failed: {exc}")

    # P2-6 / deep audit MEDIUM-10: persist scheduler_last_run via a
    # dialect-aware atomic upsert. The previous read-check-update path had
    # a small race window between two concurrent scheduler invocations.
    _upsert_scheduler_last_run(db)

    # ── LinkedIn flush is now handled by linkedin_worker ────────────────
    # P2-3 / deep audit CRITICAL-1: the LinkedIn batch flush was previously
    # called inline at the end of every scheduler tick, blocking the tick
    # for up to (POLL_TIMEOUT_SECONDS × N chunks) per cycle. It now runs in
    # a dedicated `linkedin-worker` thread (`src/services/linkedin_worker.py`)
    # that polls the queue independently. The scheduler tick only enqueues
    # work via the orchestration handlers; the worker drains it.

    logger.info(
        "run_scheduler complete: processed=%d executed=%d skipped=%d failed=%d errors=%d",
        summary.states_processed,
        summary.steps_executed,
        summary.steps_skipped,
        summary.steps_failed,
        len(summary.errors),
    )
    return summary
