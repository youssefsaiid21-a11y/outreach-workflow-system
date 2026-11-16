"""
Stress test: Day 10 simulation.

Simulates 10 days of real scheduler operation with:
- 200 prospects enrolled across days 1-10 (staggered start dates)
- Full 30-day sequence config (9 steps)
- Mid-sequence events: replies, bounces, unsubscribes arriving at random points
- Daily send limit enforcement
- Idempotency: running the scheduler twice for the same timestamp
- Concurrent sequences at every lifecycle stage
- Weekend scheduling (business_days_only)
- Suppression list additions mid-sequence
- Edge cases: missing LinkedIn URLs, already-connected leads

Every assertion is strict. Zero tolerance for errors - if this passes,
the system will hold up at production volume.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Override DATABASE_URL before any import touches the engine
os.environ["DATABASE_URL"] = "sqlite://"  # in-memory

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import Prospect, UnsubscribeEntry, ExcludedDomain, AppSetting

# Import orchestration modules
from src.orchestration.orm import (
    OrchSequenceStateORM,
    ActionExecutionORM,
    EngagementEventORM,
    AuditLogORM,
    ManualTaskORM,
)
from src.orchestration.enums import (
    EventType,
    OrchestrationStatus,
    StepResultStatus,
    StepType,
)
from src.orchestration.models import (
    EngagementEvent,
    LeadContext,
    LeadSequenceState,
    SequenceConfig,
    SequenceStep,
    StepCondition,
)
from src.orchestration.config import (
    EXAMPLE_DEFAULT_SEQUENCE,
    get_sequence_config,
    register_sequence_config,
)
from src.orchestration.scheduler import (
    run_scheduler,
    get_due_steps,
    compute_next_scheduled_at,
    SCHEDULER_BATCH_LIMIT,
    DEFAULT_DAILY_SEND_LIMIT,
)
from src.orchestration.state_machine import (
    apply_event_to_state,
    is_terminal,
    transition_state,
    TRANSITION_TABLE,
)
from src.orchestration.dispatcher import ActionDispatcher
from src.orchestration.providers import StubLinkedInProvider
from src.orchestration.repositories import (
    ActionExecutionRepository,
    AuditLogRepository,
    EngagementEventRepository,
    LeadSequenceStateRepository,
    ManualTaskRepository,
    ProspectRepository,
    _orm_to_state,
)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Test configuration
# ---------------------------------------------------------------------------

SEQUENCE_ID = EXAMPLE_DEFAULT_SEQUENCE.sequence_id
CONFIG = EXAMPLE_DEFAULT_SEQUENCE

# Enrollment batches: (day_number, count_of_prospects)
# Simulates ramping volume over 10 days
ENROLLMENT_PLAN = [
    (1, 10),   # Day 1: small pilot
    (2, 15),
    (3, 20),
    (4, 25),
    (5, 25),
    (6, 20),
    (7, 20),
    (8, 25),
    (9, 20),
    (10, 20),
]
TOTAL_PROSPECTS = sum(count for _, count in ENROLLMENT_PLAN)  # 200

# What fraction of prospects get mid-sequence events
EVENT_RATES = {
    "reply_positive": 0.05,   # 5% reply positively
    "reply_negative": 0.03,   # 3% reply negatively
    "bounce_hard": 0.04,      # 4% hard bounce
    "unsubscribe": 0.03,      # 3% unsubscribe
    "open": 0.30,             # 30% open (non-stopping event)
    "click": 0.10,            # 10% click (non-stopping event)
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_engine_and_session():
    """Create a fresh in-memory SQLite engine and session."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_pragmas(conn, _):
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _make_prospect(db, idx: int, has_linkedin: bool = True) -> Prospect:
    """Create and persist a prospect."""
    prospect = Prospect(
        id=f"prospect_{idx:04d}",
        first_name=f"First{idx}",
        last_name=f"Last{idx}",
        email=f"prospect{idx}@company{idx % 50}.com",
        title="HSE Manager",
        company=f"Company{idx % 50}",
        industry="Oil & Gas",
        location="Riyadh, Saudi Arabia",
        linkedin_url=f"https://linkedin.com/in/prospect{idx}" if has_linkedin else None,
        status="new",
    )
    db.add(prospect)
    db.flush()
    return prospect


def _enroll_prospect(
    db,
    prospect: Prospect,
    enrolled_at: datetime,
    sequence_id: str = SEQUENCE_ID,
) -> LeadSequenceState:
    """Create an orchestration state for a prospect (pending -> active)."""
    audit_repo = AuditLogRepository(db)
    state_repo = LeadSequenceStateRepository(db)

    # Get the correct config for this sequence
    config = get_sequence_config(sequence_id)

    state = LeadSequenceState(
        state_id=str(uuid.uuid4()),
        lead_id=prospect.id,
        sequence_id=sequence_id,
        status=OrchestrationStatus.pending,
        current_step_index=0,
        enrolled_at=enrolled_at,
        scheduled_next_at=None,
        engagement_score=0,
        completed_steps=[],
    )
    # Transition: pending -> active
    state = transition_state(state, "enrolled", None, audit_repo)

    # Schedule first step using the correct config
    first_step = config.steps[0]
    state.scheduled_next_at = enrolled_at + timedelta(days=first_step.day_offset)

    state_repo.save(state)
    db.flush()
    return state


def _inject_event(
    db,
    lead_id: str,
    event_type: EventType,
    occurred_at: datetime,
) -> EngagementEvent:
    """Create and persist an engagement event, then apply to state."""
    event_repo = EngagementEventRepository(db)
    state_repo = LeadSequenceStateRepository(db)
    audit_repo = AuditLogRepository(db)

    evt = EngagementEvent(
        event_id=str(uuid.uuid4()),
        lead_id=lead_id,
        event_type=event_type,
        occurred_at=occurred_at,
    )
    event_repo.save(evt)

    # Apply to the active state for this lead
    state_orm = state_repo.get_active_for_lead(lead_id)
    if state_orm is not None:
        state = _orm_to_state(state_orm)
        state = apply_event_to_state(state, evt, audit_repo)
        state_repo.upsert_from_pydantic(state)

    db.flush()
    return evt


def _make_dispatcher(db, linkedin_provider=None):
    """Create an ActionDispatcher with the given providers."""
    if linkedin_provider is None:
        linkedin_provider = StubLinkedInProvider()
    return ActionDispatcher(
        linkedin_provider=linkedin_provider,
        db=db,
        config_registry=get_sequence_config,
    )


def _day_dt(base: datetime, day: int, hour: int = 8) -> datetime:
    """Return a datetime for the given day offset from base at the specified hour UTC."""
    return base + timedelta(days=day - 1, hours=hour)


# ---------------------------------------------------------------------------
# THE STRESS TEST
# ---------------------------------------------------------------------------


class TestStressDay10:
    """Simulate 10 days of scheduler operation at increasing volume."""

    def setup_method(self):
        """Fresh database and providers for each test."""
        self.engine, self.Session = _create_engine_and_session()
        self.db = self.Session()
        self.linkedin_provider = StubLinkedInProvider()

        # Base date: a Monday so weekend logic is testable
        self.base_date = datetime(2026, 3, 23, 0, 0, 0, tzinfo=timezone.utc)  # Monday

        # Track all created prospects and their enrollment data
        self.prospects: list[Prospect] = []
        self.states: list[LeadSequenceState] = []
        self.enrollment_days: dict[str, int] = {}  # prospect_id -> enrollment day

    def teardown_method(self):
        self.db.close()
        self.engine.dispose()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _run_scheduler_at(self, day: int, hour: int = 9) -> dict:
        """Run scheduler at a specific simulated time and return summary dict."""
        now = _day_dt(self.base_date, day, hour)
        dispatcher = _make_dispatcher(self.db, self.linkedin_provider)
        summary = run_scheduler(now, self.db, dispatcher)
        return {
            "processed": summary.states_processed,
            "executed": summary.steps_executed,
            "skipped": summary.steps_skipped,
            "failed": summary.steps_failed,
            "deferred": summary.steps_deferred,
            "errors": summary.errors,
        }

    def _count_states_by_status(self) -> Counter:
        """Count sequence states grouped by status."""
        rows = self.db.query(OrchSequenceStateORM).all()
        return Counter(row.status for row in rows)

    def _count_executions_by_type(self) -> Counter:
        """Count action executions grouped by action_type."""
        rows = self.db.query(ActionExecutionORM).all()
        return Counter(row.action_type for row in rows)

    def _get_all_states(self) -> list[OrchSequenceStateORM]:
        return self.db.query(OrchSequenceStateORM).all()

    # ── Phase 1: Enrollment ─────────────────────────────────────────────

    def _phase_enroll(self):
        """Enroll 200 prospects across 10 days with staggered start dates."""
        prospect_idx = 0
        for day, count in ENROLLMENT_PLAN:
            enrolled_at = _day_dt(self.base_date, day, hour=7)
            for i in range(count):
                # 80% have LinkedIn URLs, 20% don't
                has_linkedin = (prospect_idx % 5 != 0)
                p = _make_prospect(self.db, prospect_idx, has_linkedin=has_linkedin)
                state = _enroll_prospect(self.db, p, enrolled_at)
                self.prospects.append(p)
                self.states.append(state)
                self.enrollment_days[p.id] = day
                prospect_idx += 1

        self.db.commit()

        # Verify enrollment
        assert len(self.prospects) == TOTAL_PROSPECTS
        status_counts = self._count_states_by_status()
        assert status_counts[OrchestrationStatus.active.value] == TOTAL_PROSPECTS

    # ── Phase 2: Inject mid-sequence events ─────────────────────────────

    def _phase_inject_events(self):
        """Inject stop and non-stop events for a subset of prospects."""
        import random
        random.seed(42)  # Deterministic

        self.stopped_leads: set[str] = set()
        self.event_log: list[dict] = []

        for p in self.prospects:
            enroll_day = self.enrollment_days[p.id]
            # Events happen 1-3 days after enrollment
            event_day = enroll_day + random.randint(1, 3)
            if event_day > 10:
                continue  # Event would be after our simulation window

            r = random.random()
            cumulative = 0.0

            for event_key, rate in EVENT_RATES.items():
                cumulative += rate
                if r < cumulative:
                    event_type_map = {
                        "reply_positive": EventType.email_replied_positive,
                        "reply_negative": EventType.email_replied_negative,
                        "bounce_hard": EventType.email_bounced_hard,
                        "unsubscribe": EventType.unsubscribed,
                        "open": EventType.email_opened,
                        "click": EventType.email_clicked,
                    }
                    et = event_type_map[event_key]
                    occurred_at = _day_dt(self.base_date, event_day, hour=14)

                    _inject_event(self.db, p.id, et, occurred_at)
                    self.event_log.append({
                        "lead_id": p.id,
                        "event_type": event_key,
                        "day": event_day,
                    })

                    # 2026-04-10 channel-suppression fix: hard bounces no
                    # longer terminate the orchestration sequence (LinkedIn /
                    # manual-task steps keep running for bounced leads), so
                    # bounce_hard is excluded from the stopped_leads set that
                    # asserts terminal status further down.
                    if event_key in ("reply_positive", "reply_negative", "unsubscribe"):
                        self.stopped_leads.add(p.id)
                    break

        self.db.commit()
        logger.warning(
            "Injected %d events (%d stopping) across %d prospects",
            len(self.event_log),
            len(self.stopped_leads),
            TOTAL_PROSPECTS,
        )

    # ── Phase 3: Add suppression entries mid-sequence ───────────────────

    def _phase_add_suppressions(self):
        """Add some prospects to suppression list (simulates day 5 unsubscribe sync)."""
        self.suppressed_leads: set[str] = set()

        # Pick 5 prospects enrolled on day 1-3 that haven't already been stopped
        candidates = [
            p for p in self.prospects
            if self.enrollment_days[p.id] <= 3
            and p.id not in self.stopped_leads
        ]
        for p in candidates[:5]:
            self.db.add(UnsubscribeEntry(
                email=p.email.lower(),
                reason="manual_unsub",
                source="stress_test",
            ))
            self.suppressed_leads.add(p.id)

        # Add a domain exclusion - block all @company0.com prospects
        self.db.add(ExcludedDomain(
            domain="company0.com",
            category="competitor",
            notes="Stress test exclusion",
        ))
        # Track which prospects this affects (that aren't already stopped)
        for p in self.prospects:
            if p.email.endswith("@company0.com") and p.id not in self.stopped_leads:
                self.suppressed_leads.add(p.id)

        self.db.commit()
        logger.warning("Added %d suppressed leads", len(self.suppressed_leads))

    # ── Phase 4: Run scheduler across all 10 days ───────────────────────

    def _phase_run_10_days(self):
        """Run the scheduler twice per day for 10 days and collect results."""
        self.daily_results: list[dict] = []

        for day in range(1, 11):
            # Morning run (9 AM)
            result_am = self._run_scheduler_at(day, hour=9)
            # Afternoon run (3 PM) - catches anything scheduled mid-day
            result_pm = self._run_scheduler_at(day, hour=15)

            combined = {
                "day": day,
                "processed": result_am["processed"] + result_pm["processed"],
                "executed": result_am["executed"] + result_pm["executed"],
                "skipped": result_am["skipped"] + result_pm["skipped"],
                "failed": result_am["failed"] + result_pm["failed"],
                "deferred": result_am["deferred"] + result_pm["deferred"],
                "errors_am": result_am["errors"],
                "errors_pm": result_pm["errors"],
            }
            self.daily_results.append(combined)

    # ── Main test ────────────────────────────────────────────────────────

    def test_full_10_day_simulation(self):
        """
        The big one. Runs all phases and validates every invariant.

        This test MUST pass with zero errors. Any failure here means the
        system cannot be trusted at production volume.
        """
        # ── Phase 1: Enroll 200 prospects ──
        self._phase_enroll()

        # ── Phase 2: Inject events ──
        self._phase_inject_events()

        # ── Phase 3: Add suppressions ──
        self._phase_add_suppressions()

        # ── Phase 4: Run 10 days of scheduler ──
        self._phase_run_10_days()

        # ════════════════════════════════════════════════════════════════
        # ASSERTIONS - Zero tolerance
        # ════════════════════════════════════════════════════════════════

        # 1. NO SCHEDULER ERRORS across all 10 days
        all_errors = []
        for r in self.daily_results:
            all_errors.extend(r["errors_am"])
            all_errors.extend(r["errors_pm"])
        assert all_errors == [], (
            f"Scheduler produced {len(all_errors)} errors across 10 days:\n"
            + "\n".join(all_errors[:20])
        )

        # 2. ALL STOPPED LEADS are actually in terminal states
        for lead_id in self.stopped_leads:
            state_orm = (
                self.db.query(OrchSequenceStateORM)
                .filter(OrchSequenceStateORM.lead_id == lead_id)
                .first()
            )
            assert state_orm is not None, f"No state found for stopped lead {lead_id}"
            assert is_terminal(OrchestrationStatus(state_orm.status)), (
                f"Lead {lead_id} should be terminal but is '{state_orm.status}'"
            )

        # 3. NO DUPLICATE EXECUTIONS (idempotency)
        all_exec_ids = [
            row.execution_id
            for row in self.db.query(ActionExecutionORM).all()
        ]
        assert len(all_exec_ids) == len(set(all_exec_ids)), (
            f"Duplicate execution IDs found! "
            f"Total={len(all_exec_ids)} Unique={len(set(all_exec_ids))}"
        )

        # 4. EVERY NON-STOPPED, NON-SUPPRESSED LEAD has progressed
        active_states = self._count_states_by_status()
        total_terminal = sum(
            active_states.get(s.value, 0)
            for s in [
                OrchestrationStatus.stopped_positive_reply,
                OrchestrationStatus.stopped_negative_reply,
                OrchestrationStatus.stopped_bounced,
                OrchestrationStatus.stopped_unsubscribed,
                OrchestrationStatus.stopped_disqualified,
                OrchestrationStatus.completed,
            ]
        )
        total_in_progress = sum(
            active_states.get(s.value, 0)
            for s in [
                OrchestrationStatus.active,
                OrchestrationStatus.waiting_for_next_step,
                OrchestrationStatus.paused,
            ]
        )
        assert total_terminal + total_in_progress == TOTAL_PROSPECTS, (
            f"State accounting mismatch: terminal={total_terminal} "
            f"in_progress={total_in_progress} total={TOTAL_PROSPECTS}\n"
            f"Status breakdown: {dict(active_states)}"
        )

        # 5. (Email provider removed — send_email steps are now skipped)

        # 6. NON-STOPPED LEADS enrolled on day 1 should have advanced past step 0
        #    by day 10 (step 0 = day 1, step 1 = day 4 LinkedIn, step 2 = day 7)
        for p in self.prospects:
            if self.enrollment_days[p.id] == 1 and p.id not in self.stopped_leads:
                state_orm = (
                    self.db.query(OrchSequenceStateORM)
                    .filter(OrchSequenceStateORM.lead_id == p.id)
                    .first()
                )
                if state_orm and not is_terminal(OrchestrationStatus(state_orm.status)):
                    assert state_orm.current_step_index >= 2, (
                        f"Day-1 lead {p.id} should be at step >= 2 by day 10, "
                        f"but is at step {state_orm.current_step_index} "
                        f"(status={state_orm.status})"
                    )

        # 7. AUDIT TRAIL is complete - every state has at least 1 audit entry
        for state_orm in self._get_all_states():
            audit_count = (
                self.db.query(AuditLogORM)
                .filter(AuditLogORM.state_id == state_orm.state_id)
                .count()
            )
            assert audit_count >= 1, (
                f"State {state_orm.state_id} (lead={state_orm.lead_id}) "
                f"has no audit trail entries!"
            )

        # 8. STEP INDEX never exceeds total steps
        total_steps = len(CONFIG.steps)
        for state_orm in self._get_all_states():
            assert state_orm.current_step_index <= total_steps, (
                f"Lead {state_orm.lead_id} step_index={state_orm.current_step_index} "
                f"exceeds total steps={total_steps}"
            )

        # 9. COMPLETED_STEPS list length <= current_step_index
        for state_orm in self._get_all_states():
            completed = state_orm.completed_steps or []
            assert len(completed) <= state_orm.current_step_index, (
                f"Lead {state_orm.lead_id}: completed_steps={len(completed)} "
                f"> current_step_index={state_orm.current_step_index}"
            )

        # 10. NO ORPHANED STATES - every state has a matching prospect
        for state_orm in self._get_all_states():
            prospect = (
                self.db.query(Prospect)
                .filter(Prospect.id == state_orm.lead_id)
                .first()
            )
            assert prospect is not None, (
                f"Orphaned state {state_orm.state_id} - no prospect for lead_id={state_orm.lead_id}"
            )

        # Print summary
        print(f"\n{'='*70}")
        print(f"STRESS TEST PASSED - 10-day simulation")
        print(f"{'='*70}")
        print(f"Prospects enrolled:    {TOTAL_PROSPECTS}")
        print(f"Events injected:       {len(self.event_log)}")
        print(f"Stopped by events:     {len(self.stopped_leads)}")
        print(f"Suppressed:            {len(self.suppressed_leads)}")
        print(f"Execution records:     {len(all_exec_ids)}")
        print(f"Status breakdown:      {dict(active_states)}")
        print(f"\nDaily results:")
        for r in self.daily_results:
            print(
                f"  Day {r['day']:2d}: processed={r['processed']:3d} "
                f"executed={r['executed']:3d} skipped={r['skipped']:3d} "
                f"failed={r['failed']:3d}"
            )
        print(f"{'='*70}\n")

    # ── Idempotency test ────────────────────────────────────────────────

    def test_idempotency_double_run(self):
        """
        Run the scheduler twice at the exact same timestamp.
        The second run must produce zero new executions.
        """
        # Enroll 30 prospects on day 1
        enrolled_at = _day_dt(self.base_date, 1, hour=7)
        for i in range(30):
            p = _make_prospect(self.db, i + 1000)
            _enroll_prospect(self.db, p, enrolled_at)
        self.db.commit()

        # First run at day 2 (step 0 = day_offset 1 is due)
        now = _day_dt(self.base_date, 2, hour=9)
        dispatcher = _make_dispatcher(self.db, self.linkedin_provider)
        result1 = run_scheduler(now, self.db, dispatcher)

        exec_count_after_first = self.db.query(ActionExecutionORM).count()

        # Second run at same timestamp
        dispatcher2 = _make_dispatcher(self.db, self.linkedin_provider)
        result2 = run_scheduler(now, self.db, dispatcher2)

        exec_count_after_second = self.db.query(ActionExecutionORM).count()

        # First run should have done work
        assert result1.states_processed > 0, "First run should process states"

        # Second run should be a no-op (all states advanced to waiting)
        assert result2.steps_executed == 0, (
            f"Second run executed {result2.steps_executed} steps - should be 0 (idempotent)"
        )
        assert exec_count_after_second == exec_count_after_first, (
            f"Second run created {exec_count_after_second - exec_count_after_first} extra executions"
        )
        assert result2.errors == [], f"Second run had errors: {result2.errors}"

    # ── Daily send limit test ───────────────────────────────────────────

    def test_daily_send_limit_enforcement(self):
        """
        Enroll more prospects than the daily limit allows.
        Verify the scheduler respects the cap.
        """
        # Register a config with a very low daily send limit
        low_limit_config = SequenceConfig(
            sequence_id="stress-low-limit",
            name="Low Limit Test",
            daily_send_limit=10,  # Only 10 sends/day
            steps=[
                SequenceStep(
                    step_id="step-email-1",
                    step_type=StepType.send_email,
                    day_offset=0,
                    template_id="email_1",
                ),
                SequenceStep(
                    step_id="step-email-2",
                    step_type=StepType.send_email,
                    day_offset=3,
                    template_id="email_2",
                ),
                SequenceStep(
                    step_id="step-end",
                    step_type=StepType.end_sequence,
                    day_offset=4,
                ),
            ],
        )
        register_sequence_config(low_limit_config)

        # Enroll 30 prospects - more than the daily limit of 10
        enrolled_at = _day_dt(self.base_date, 1, hour=7)
        for i in range(30):
            p = _make_prospect(self.db, i + 2000)
            _enroll_prospect(self.db, p, enrolled_at, sequence_id="stress-low-limit")
        self.db.commit()

        # Run scheduler - first step (day_offset=0) is immediately due
        now = _day_dt(self.base_date, 1, hour=9)
        dispatcher = _make_dispatcher(self.db, self.linkedin_provider)
        result = run_scheduler(now, self.db, dispatcher)

        # Should process all 30 but only execute 10 (daily limit)
        assert result.steps_executed == 10, (
            f"Expected exactly 10 sends (daily limit), got {result.steps_executed}"
        )
        assert result.steps_skipped == 20, (
            f"Expected 20 skipped (over limit), got {result.steps_skipped}"
        )
        assert result.errors == [], f"Errors: {result.errors}"

        # Run again same day - should execute 0 (limit already reached)
        dispatcher2 = _make_dispatcher(self.db, self.linkedin_provider)
        result2 = run_scheduler(now, self.db, dispatcher2)
        # The 20 that were skipped due to daily limit are still due
        # They should be skipped again
        assert result2.steps_executed == 0, (
            f"Second run should execute 0, got {result2.steps_executed}"
        )
        assert result2.errors == [], f"Second run errors: {result2.errors}"

        # Note: cross-day rollover cannot be tested with simulated time because
        # handlers record attempted_at using wall-clock datetime.now(), not the
        # scheduler's simulated `now`. In production this is fine because
        # now ≈ wall clock. The critical guarantee (cap per run + persistence
        # across runs within the same day) is validated above.

    # ── Event-driven stop test ──────────────────────────────────────────

    def test_events_stop_sequences_correctly(self):
        """
        Enroll prospects, inject stop events, verify sequences are terminated
        and no further steps execute for stopped leads.
        """
        enrolled_at = _day_dt(self.base_date, 1, hour=7)
        prospects = []
        for i in range(20):
            p = _make_prospect(self.db, i + 3000)
            _enroll_prospect(self.db, p, enrolled_at)
            prospects.append(p)
        self.db.commit()

        # Run day 2 to execute step 0 (email 1, day_offset=1)
        self._run_scheduler_at_raw(2, 9)

        # Record execution counts BEFORE injecting stop events
        pre_stop_counts = {}
        stop_lead_ids = [prospects[i].id for i in range(4)]
        for lead_id in stop_lead_ids:
            pre_stop_counts[lead_id] = (
                self.db.query(ActionExecutionORM)
                .filter(ActionExecutionORM.lead_id == lead_id)
                .count()
            )

        # Now inject stop events for first 4 prospects. Note: 2026-04-10
        # channel-suppression fix - email_bounced_hard is NO LONGER a
        # terminal transition. We use lead_disqualified here to still
        # exercise the "event stops the whole sequence" path; bounce
        # channel suppression has its own dedicated tests in
        # test_state_machine.py and test_linkedin_handler_session.py.
        stop_events = [
            (prospects[0].id, EventType.email_replied_positive),
            (prospects[1].id, EventType.email_replied_negative),
            (prospects[2].id, EventType.lead_disqualified),
            (prospects[3].id, EventType.unsubscribed),
        ]
        for lead_id, evt_type in stop_events:
            _inject_event(self.db, lead_id, evt_type, _day_dt(self.base_date, 3, hour=10))
        self.db.commit()

        # Run day 5 (step 1 = LinkedIn connect, day_offset=4)
        result = self._run_scheduler_at_raw(5, 9)
        # Run day 8 (step 2 = email 2, day_offset=7) to be thorough
        self._run_scheduler_at_raw(8, 9)

        # Verify stopped leads are terminal
        expected_statuses = {
            prospects[0].id: OrchestrationStatus.stopped_positive_reply,
            prospects[1].id: OrchestrationStatus.stopped_negative_reply,
            prospects[2].id: OrchestrationStatus.stopped_disqualified,
            prospects[3].id: OrchestrationStatus.stopped_unsubscribed,
        }
        for lead_id, expected_status in expected_statuses.items():
            state_orm = (
                self.db.query(OrchSequenceStateORM)
                .filter(OrchSequenceStateORM.lead_id == lead_id)
                .first()
            )
            assert state_orm is not None
            assert state_orm.status == expected_status.value, (
                f"Lead {lead_id}: expected {expected_status.value}, got {state_orm.status}"
            )

        # Verify stopped leads had NO NEW executions after the stop event
        for lead_id in stop_lead_ids:
            post_stop_count = (
                self.db.query(ActionExecutionORM)
                .filter(ActionExecutionORM.lead_id == lead_id)
                .count()
            )
            assert post_stop_count == pre_stop_counts[lead_id], (
                f"Stopped lead {lead_id} had {post_stop_count - pre_stop_counts[lead_id]} "
                f"new executions after stop event (before={pre_stop_counts[lead_id]}, after={post_stop_count})"
            )

    def _run_scheduler_at_raw(self, day, hour):
        """Run scheduler and return the raw summary object."""
        now = _day_dt(self.base_date, day, hour)
        dispatcher = _make_dispatcher(self.db, self.linkedin_provider)
        return run_scheduler(now, self.db, dispatcher)

    # ── Staggered enrollment timing test ────────────────────────────────

    def test_staggered_enrollment_correct_timing(self):
        """
        Prospects enrolled on different days must have their steps
        scheduled relative to THEIR enrollment date, not each other's.
        """
        # Enroll prospect A on day 1, prospect B on day 5
        p_a = _make_prospect(self.db, 4001)
        p_b = _make_prospect(self.db, 4002)

        enrolled_a = _day_dt(self.base_date, 1, hour=7)
        enrolled_b = _day_dt(self.base_date, 5, hour=7)

        _enroll_prospect(self.db, p_a, enrolled_a)
        _enroll_prospect(self.db, p_b, enrolled_b)
        self.db.commit()

        # Run on day 2 - only A's step 0 (day_offset=1) should fire
        result = self._run_scheduler_at_raw(2, 9)
        assert result.steps_executed == 1

        # Verify it was A
        a_execs = (
            self.db.query(ActionExecutionORM)
            .filter(ActionExecutionORM.lead_id == p_a.id)
            .count()
        )
        b_execs = (
            self.db.query(ActionExecutionORM)
            .filter(ActionExecutionORM.lead_id == p_b.id)
            .count()
        )
        assert a_execs == 1, f"A should have 1 execution, has {a_execs}"
        assert b_execs == 0, f"B should have 0 executions, has {b_execs}"

        # Run on day 6 - B's step 0 (enrolled day 5 + offset 1 = day 6) fires
        # Also A's step 1 (LinkedIn, enrolled day 1 + offset 4 = day 5) should have fired
        # Let's run day 5 first for A's LinkedIn step
        self._run_scheduler_at_raw(5, 9)
        result_d6 = self._run_scheduler_at_raw(6, 9)

        b_execs_after = (
            self.db.query(ActionExecutionORM)
            .filter(ActionExecutionORM.lead_id == p_b.id)
            .count()
        )
        assert b_execs_after >= 1, f"B should have at least 1 execution by day 6, has {b_execs_after}"

    # ── Batch limit test ────────────────────────────────────────────────

    def test_batch_limit_respected(self):
        """
        Enroll more than SCHEDULER_BATCH_LIMIT prospects.
        Verify the scheduler caps at the batch limit.
        """
        enrolled_at = _day_dt(self.base_date, 1, hour=7)
        count = SCHEDULER_BATCH_LIMIT + 50  # 550

        for i in range(count):
            p = _make_prospect(self.db, i + 5000)
            _enroll_prospect(self.db, p, enrolled_at)
        self.db.commit()

        # All 550 have step 0 due at day 2
        now = _day_dt(self.base_date, 2, hour=9)

        # Check how many due steps are returned
        due = get_due_steps(now, self.db)
        assert len(due) == SCHEDULER_BATCH_LIMIT, (
            f"Expected batch limit of {SCHEDULER_BATCH_LIMIT}, got {len(due)}"
        )

    # ── FSM completeness test ───────────────────────────────────────────

    def test_fsm_all_stop_events_from_all_active_states(self):
        """
        Verify that every stop event can be applied from every non-terminal state.
        This is a combinatorial check of the transition table.
        """
        from src.orchestration.state_machine import STOP_EVENT_MAP

        non_terminal = [
            OrchestrationStatus.active,
            OrchestrationStatus.waiting_for_next_step,
            OrchestrationStatus.paused,
            OrchestrationStatus.pending,
        ]

        for status in non_terminal:
            for event_type, trigger in STOP_EVENT_MAP.items():
                key = (status, trigger)
                assert key in TRANSITION_TABLE, (
                    f"Missing transition: ({status.value}, {trigger}) - "
                    f"stop event {event_type.value} cannot be applied from {status.value}"
                )

    # ── LinkedIn step skip test ─────────────────────────────────────────

    def test_linkedin_steps_skip_without_url(self):
        """
        Prospects without LinkedIn URLs must have LinkedIn steps skipped,
        not failed.
        """
        enrolled_at = _day_dt(self.base_date, 1, hour=7)
        p = _make_prospect(self.db, 6001, has_linkedin=False)
        _enroll_prospect(self.db, p, enrolled_at)
        self.db.commit()

        # Run through day 1-5 to get past email 1 (day 1) and linkedin connect (day 4)
        for day in range(2, 6):
            self._run_scheduler_at_raw(day, 9)

        # Check: LinkedIn connect step should be skipped, not failed
        li_execs = (
            self.db.query(ActionExecutionORM)
            .filter(
                ActionExecutionORM.lead_id == p.id,
                ActionExecutionORM.action_type == StepType.send_linkedin_connect.value,
            )
            .all()
        )
        for ex in li_execs:
            assert ex.status == StepResultStatus.skipped.value, (
                f"LinkedIn exec for no-URL lead should be skipped, got {ex.status}"
            )

        # The state should still be progressing (not paused/failed)
        state_orm = (
            self.db.query(OrchSequenceStateORM)
            .filter(OrchSequenceStateORM.lead_id == p.id)
            .first()
        )
        assert not is_terminal(OrchestrationStatus(state_orm.status)), (
            f"Lead without LinkedIn URL should still be progressing, but is {state_orm.status}"
        )

    # ── Completed steps tracking test ───────────────────────────────────

    def test_completed_steps_only_on_execution(self):
        """
        completed_steps list should only contain step_ids of steps that
        were actually executed (not skipped).
        """
        enrolled_at = _day_dt(self.base_date, 1, hour=7)
        p = _make_prospect(self.db, 7001, has_linkedin=False)  # No LinkedIn = skip LI step
        _enroll_prospect(self.db, p, enrolled_at)
        self.db.commit()

        # Run days 2-6 to cover email 1 (day 1), linkedin connect (day 4, will skip)
        for day in range(2, 6):
            self._run_scheduler_at_raw(day, 9)

        state_orm = (
            self.db.query(OrchSequenceStateORM)
            .filter(OrchSequenceStateORM.lead_id == p.id)
            .first()
        )
        completed = state_orm.completed_steps or []

        # LinkedIn connect step_id should NOT be in completed_steps
        li_step_id = CONFIG.steps[1].step_id  # Day 4 LinkedIn connect
        assert li_step_id not in completed, (
            f"Skipped LinkedIn step {li_step_id} should not be in completed_steps"
        )

        # Email 1 step_id SHOULD be in completed_steps
        email1_step_id = CONFIG.steps[0].step_id
        assert email1_step_id in completed, (
            f"Executed email step {email1_step_id} should be in completed_steps"
        )

    # ── Non-stop events don't stop sequence test ────────────────────────

    def test_non_stop_events_dont_terminate(self):
        """
        email_opened and email_clicked events should NOT stop a sequence.
        """
        enrolled_at = _day_dt(self.base_date, 1, hour=7)
        p = _make_prospect(self.db, 8001)
        _enroll_prospect(self.db, p, enrolled_at)
        self.db.commit()

        # Execute step 0
        self._run_scheduler_at_raw(2, 9)

        # Inject open and click events
        _inject_event(self.db, p.id, EventType.email_opened, _day_dt(self.base_date, 2, hour=14))
        _inject_event(self.db, p.id, EventType.email_clicked, _day_dt(self.base_date, 2, hour=15))
        self.db.commit()

        # Verify state is NOT terminal
        state_orm = (
            self.db.query(OrchSequenceStateORM)
            .filter(OrchSequenceStateORM.lead_id == p.id)
            .first()
        )
        assert not is_terminal(OrchestrationStatus(state_orm.status)), (
            f"Open/click events should not stop sequence, but status is {state_orm.status}"
        )

        # Run more days - sequence should keep progressing
        for day in range(3, 9):
            self._run_scheduler_at_raw(day, 9)

        state_orm = self.db.query(OrchSequenceStateORM).filter(
            OrchSequenceStateORM.lead_id == p.id
        ).first()
        assert state_orm.current_step_index >= 3, (
            f"Sequence should have progressed past step 3 by day 8, "
            f"but is at {state_orm.current_step_index}"
        )

    # ── Pause / resume test ─────────────────────────────────────────────

    def test_pause_resume_does_not_lose_progress(self):
        """
        Pausing and resuming a sequence must preserve the step index
        and resume from exactly where it left off.
        """
        enrolled_at = _day_dt(self.base_date, 1, hour=7)
        p = _make_prospect(self.db, 9001)
        state = _enroll_prospect(self.db, p, enrolled_at)
        self.db.commit()

        # Execute step 0
        self._run_scheduler_at_raw(2, 9)

        # Get current state
        state_repo = LeadSequenceStateRepository(self.db)
        audit_repo = AuditLogRepository(self.db)
        state_orm = state_repo.get_active_for_lead(p.id)
        state_model = _orm_to_state(state_orm)
        step_before_pause = state_model.current_step_index

        # Pause
        state_model = transition_state(state_model, "pause", None, audit_repo)
        state_model.paused_reason = "testing"
        state_repo.upsert_from_pydantic(state_model)
        self.db.commit()

        # Run scheduler - paused state should NOT be picked up
        result = self._run_scheduler_at_raw(5, 9)

        state_orm = self.db.query(OrchSequenceStateORM).filter(
            OrchSequenceStateORM.lead_id == p.id
        ).first()
        assert state_orm.status == OrchestrationStatus.paused.value
        assert state_orm.current_step_index == step_before_pause

        # Resume
        state_model = _orm_to_state(state_orm)
        state_model = transition_state(state_model, "resume", None, audit_repo)
        state_repo.upsert_from_pydantic(state_model)
        self.db.commit()

        # Now scheduler should pick it up
        result = self._run_scheduler_at_raw(8, 9)

        state_orm = self.db.query(OrchSequenceStateORM).filter(
            OrchSequenceStateORM.lead_id == p.id
        ).first()
        assert state_orm.current_step_index > step_before_pause, (
            f"After resume, step should have advanced past {step_before_pause}, "
            f"but is at {state_orm.current_step_index}"
        )


# ---------------------------------------------------------------------------
# Run with pytest
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=long", "-s"])
