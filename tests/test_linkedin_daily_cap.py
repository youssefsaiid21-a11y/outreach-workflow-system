"""
LinkedIn connect daily-cap enforcement - unit-level test for the
scheduler-side rolling-send logic added 2026-04-10.

What we're validating
---------------------
The scheduler now tracks per-user LinkedIn connect slots consumed
today (counting queued/processing/done rows in ``linkedin_queue``
since UTC midnight) and defers over-cap states to the next working
day instead of letting the overflow slip through to PhantomBuster
where it would silently fail as "unknown: no result row for url".

Two scenarios:
  1. Clean slate. Cap=5, 10 due states → 5 execute, 5 deferred, with
     the deferred states having ``scheduled_next_at`` bumped to the
     next working day at 00:05 UTC.
  2. Mid-day restart. Cap=5, 3 already-done queue rows exist,
     10 due states → 2 execute (only 2 slots left), 8 deferred.

Approach
--------
Uses an in-memory SQLite DB mirroring the existing test_stress_day10
pattern. Avoids the PhantomBuster HTTP client entirely by injecting a
pass-through LinkedIn provider that does NOT subclass
``StubLinkedInProvider`` - the handler's StubLinkedInProvider short
circuit otherwise skips the whole execute path and the cache
increment never fires.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

# Path setup - mirror test_stress_day10.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ["DATABASE_URL"] = "sqlite://"

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import (
    LinkedInQueue,
    LinkedInQueueStatus,
    Prospect,
    User,
)

from src.orchestration.enums import OrchestrationStatus, StepType
from src.orchestration.models import (
    LeadSequenceState,
    SequenceConfig,
    SequenceStep,
    StepCondition,
)
from src.orchestration.config import register_sequence_config
from src.orchestration.repositories import (
    AuditLogRepository,
    LeadSequenceStateRepository,
)
from src.orchestration.state_machine import transition_state
from src.orchestration.scheduler import (
    _count_todays_linkedin_connects,
    _next_working_day_start,
    run_scheduler,
)
from src.orchestration.dispatcher import ActionDispatcher
from src.orchestration.providers import (
    LinkedInProvider,
    LinkedInResult,
)


# ── Test fixtures ────────────────────────────────────────────────────────────


def _create_engine_and_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autocommit=False, autoflush=False)


class _PassthroughLinkedInProvider(LinkedInProvider):
    """
    Non-stub LinkedIn provider. The handler's ``isinstance(_li,
    StubLinkedInProvider)`` check skips the whole dispatch path if we
    use the stub, so this is a direct subclass that simply returns
    success without any Phantom integration. The handler still writes
    a ``linkedin_queue`` row via ``queue_linkedin_action_no_commit``
    which is exactly what ``_count_todays_linkedin_connects`` reads
    from in subsequent ticks.
    """

    def send_connect_request(self, lead_id, linkedin_url, note_template_id):
        return LinkedInResult(success=True, provider_message_id=f"li-{lead_id}")

    def send_linkedin_message(self, lead_id, linkedin_url, template_id):
        return LinkedInResult(success=True, provider_message_id=f"lim-{lead_id}")

    def check_connection_status(self, lead_id, linkedin_url):
        return False


def _make_user(db, uid: str = "user-cap-test") -> User:
    u = User(
        id=uid,
        email=f"{uid}@example.com",
        name="Cap Test",
        password_hash="x",
    )
    db.add(u)
    db.flush()
    return u


def _make_prospect_with_user(db, idx: int, user_id: str) -> Prospect:
    p = Prospect(
        id=f"p-cap-{idx:04d}",
        user_id=user_id,
        first_name=f"First{idx}",
        last_name=f"Last{idx}",
        email=f"cap{idx}@test.com",
        linkedin_url=f"https://linkedin.com/in/cap{idx}",
        status="new",
    )
    db.add(p)
    db.flush()
    return p


def _enroll(db, prospect, sequence_id: str, enrolled_at: datetime) -> LeadSequenceState:
    from src.orchestration.config import get_sequence_config
    audit_repo = AuditLogRepository(db)
    state_repo = LeadSequenceStateRepository(db)
    cfg = get_sequence_config(sequence_id)

    state = LeadSequenceState(
        state_id=str(uuid.uuid4()),
        lead_id=prospect.id,
        sequence_id=sequence_id,
        status=OrchestrationStatus.pending,
        current_step_index=0,
        enrolled_at=enrolled_at,
        scheduled_next_at=enrolled_at + timedelta(days=cfg.steps[0].day_offset),
        engagement_score=0,
        completed_steps=[],
    )
    state = transition_state(state, "enrolled", None, audit_repo)
    state_repo.save(state)

    # Multi-tenant isolation: backfill user_id on the ORM row so the
    # scheduler's per-user filter picks it up.
    row = state_repo.get(state.state_id)
    if row is not None:
        row.user_id = prospect.user_id
    db.flush()
    return state


def _register_li_only_sequence(sequence_id: str = "cap-test-seq"):
    cfg = SequenceConfig(
        sequence_id=sequence_id,
        name="Cap Test LI-only",
        timezone="UTC",
        business_days_only=False,
        scoring_threshold=5,
        steps=[
            SequenceStep(
                step_id="cap-test-step-li-connect",
                step_type=StepType.send_linkedin_connect,
                day_offset=0,
                template_id=None,
                conditions=[
                    StepCondition(condition_type="no_reply", value=None),
                    StepCondition(condition_type="no_unsubscribe", value=None),
                    StepCondition(condition_type="linkedin_url_exists", value=None),
                ],
            ),
            SequenceStep(
                step_id="cap-test-step-end",
                step_type=StepType.end_sequence,
                day_offset=1,
            ),
        ],
    )
    register_sequence_config(cfg)
    return cfg


def _make_dispatcher(db):
    from src.orchestration.config import get_sequence_config
    return ActionDispatcher(
        linkedin_provider=_PassthroughLinkedInProvider(),
        db=db,
        config_registry=get_sequence_config,
    )


# ── Tests ────────────────────────────────────────────────────────────────────


def test_helper_next_working_day_skips_weekend():
    # Friday 2026-04-10 → Monday 2026-04-13 00:05 UTC
    fri = datetime(2026, 4, 10, 14, 30, tzinfo=timezone.utc)
    assert _next_working_day_start(fri) == datetime(2026, 4, 13, 0, 5, tzinfo=timezone.utc)

    # Saturday → Monday
    sat = datetime(2026, 4, 11, 6, 0, tzinfo=timezone.utc)
    assert _next_working_day_start(sat) == datetime(2026, 4, 13, 0, 5, tzinfo=timezone.utc)

    # Wednesday → Thursday (no weekend skip)
    wed = datetime(2026, 4, 8, 9, 0, tzinfo=timezone.utc)
    assert _next_working_day_start(wed) == datetime(2026, 4, 9, 0, 5, tzinfo=timezone.utc)


def test_count_todays_linkedin_connects_respects_boundary():
    engine, Session = _create_engine_and_session()
    db = Session()
    try:
        user = _make_user(db)
        other = _make_user(db, uid="other-user")
        # linkedin_queue.prospect_id has a FK to prospects so we need
        # real prospects to attach the seed rows to.
        px = _make_prospect_with_user(db, 1, user.id)
        db.commit()

        now = datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = today_start - timedelta(hours=2)

        # 2 rows today (one done, one processing) → count
        # 1 row today with status=failed → does NOT count
        # 1 row today with status=skipped → does NOT count
        # 1 row yesterday → does NOT count
        db.add_all([
            LinkedInQueue(
                id="q-1", prospect_id=px.id, user_id=user.id,
                action_type="connect", linkedin_url="u1",
                status=LinkedInQueueStatus.done, created_at=today_start + timedelta(hours=1),
            ),
            LinkedInQueue(
                id="q-2", prospect_id=px.id, user_id=user.id,
                action_type="connect", linkedin_url="u2",
                status=LinkedInQueueStatus.processing, created_at=today_start + timedelta(hours=2),
            ),
            LinkedInQueue(
                id="q-3", prospect_id=px.id, user_id=user.id,
                action_type="connect", linkedin_url="u3",
                status=LinkedInQueueStatus.failed, created_at=today_start + timedelta(hours=3),
            ),
            LinkedInQueue(
                id="q-4", prospect_id=px.id, user_id=user.id,
                action_type="connect", linkedin_url="u4",
                status=LinkedInQueueStatus.skipped, created_at=today_start + timedelta(hours=4),
            ),
            LinkedInQueue(
                id="q-5", prospect_id=px.id, user_id=user.id,
                action_type="connect", linkedin_url="u5",
                status=LinkedInQueueStatus.done, created_at=yesterday,
            ),
            # Different user, today → not counted
            LinkedInQueue(
                id="q-6", prospect_id=px.id, user_id=other.id,
                action_type="connect", linkedin_url="u6",
                status=LinkedInQueueStatus.done, created_at=today_start + timedelta(hours=5),
            ),
            # Message action, today → not counted (we cap connects only)
            LinkedInQueue(
                id="q-7", prospect_id=px.id, user_id=user.id,
                action_type="message", linkedin_url="u7",
                status=LinkedInQueueStatus.done, created_at=today_start + timedelta(hours=6),
            ),
        ])
        db.commit()

        got = _count_todays_linkedin_connects(db, user.id, now)
        assert got == 2, f"expected 2 slots consumed today, got {got}"

        # No user_id → returns 0 (cap is meaningless without a user scope)
        assert _count_todays_linkedin_connects(db, None, now) == 0
    finally:
        db.close()


def test_scheduler_caps_connects_and_defers_overflow(monkeypatch):
    engine, Session = _create_engine_and_session()
    db = Session()
    try:
        # Force cap = 5 regardless of env
        monkeypatch.setattr(
            "src.orchestration.scheduler._resolve_user_linkedin_daily_cap",
            lambda db, uid: 5,
        )
        # Use a Wednesday so _next_working_day_start gives Thursday
        # (no weekend ambiguity in the assertion).
        now = datetime(2026, 4, 8, 9, 0, tzinfo=timezone.utc)  # Wed
        expected_defer_to = datetime(2026, 4, 9, 0, 5, tzinfo=timezone.utc)  # Thu 00:05

        user = _make_user(db)
        _register_li_only_sequence("cap-test-seq-1")

        # Enroll 10 prospects, all due `now`.
        prospects = []
        for i in range(10):
            p = _make_prospect_with_user(db, i, user.id)
            prospects.append(p)
            _enroll(db, p, "cap-test-seq-1", now - timedelta(minutes=1))
        db.commit()

        dispatcher = _make_dispatcher(db)
        result = run_scheduler(now, db, dispatcher, user_id=user.id)

        assert result.steps_executed == 5, (
            f"expected 5 executed (cap), got {result.steps_executed}"
        )
        assert result.steps_deferred == 5, (
            f"expected 5 deferred (10 due - 5 cap), got {result.steps_deferred}"
        )

        # Verify the 5 deferred states have scheduled_next_at bumped to
        # the next working day start.
        from src.orchestration.orm import OrchSequenceStateORM
        deferred_rows = (
            db.query(OrchSequenceStateORM)
            .filter(OrchSequenceStateORM.user_id == user.id)
            .filter(OrchSequenceStateORM.current_step_index == 0)  # not yet advanced
            .all()
        )
        assert len(deferred_rows) == 5, f"expected 5 deferred state rows, got {len(deferred_rows)}"
        for row in deferred_rows:
            # SQLite stores whatever we wrote. Compare as naive since
            # Pydantic serialisation may drop tz info on the roundtrip.
            stored = row.scheduled_next_at
            if stored.tzinfo is None:
                stored = stored.replace(tzinfo=timezone.utc)
            assert stored == expected_defer_to, (
                f"deferred state scheduled_next_at={stored}, expected {expected_defer_to}"
            )

        # Verify exactly 5 linkedin_queue rows were written (one per
        # executed state), all with action_type=connect. Note that
        # queue_linkedin_action_no_commit does NOT set user_id - that
        # column is backfilled later by linkedin_worker before launch.
        # So filter by action_type only.
        queue_rows = db.query(LinkedInQueue).filter(
            LinkedInQueue.action_type == "connect",
        ).all()
        assert len(queue_rows) == 5, (
            f"expected 5 linkedin_queue rows written, got {len(queue_rows)}"
        )
    finally:
        db.close()


def test_scheduler_cap_accounts_for_preexisting_queue_rows(monkeypatch):
    """
    Mid-day restart: cap=5, 3 queue rows already exist from earlier in
    the day. Only 2 more states should execute; the rest deferred.
    """
    engine, Session = _create_engine_and_session()
    db = Session()
    try:
        monkeypatch.setattr(
            "src.orchestration.scheduler._resolve_user_linkedin_daily_cap",
            lambda db, uid: 5,
        )
        now = datetime(2026, 4, 8, 9, 0, tzinfo=timezone.utc)  # Wed
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        user = _make_user(db)
        _register_li_only_sequence("cap-test-seq-2")

        # Real prospect so linkedin_queue.prospect_id FK is satisfied
        seed_prospect = _make_prospect_with_user(db, 999, user.id)
        db.commit()

        # Seed 3 pre-existing queue rows (earlier this morning)
        for i in range(3):
            db.add(LinkedInQueue(
                id=f"pre-{i}", prospect_id=seed_prospect.id,
                user_id=user.id,
                action_type="connect",
                linkedin_url=f"u{i}",
                status=LinkedInQueueStatus.done,
                created_at=today_start + timedelta(hours=1),
            ))
        db.commit()

        # Enroll 10 fresh prospects
        for i in range(10):
            p = _make_prospect_with_user(db, i + 100, user.id)
            _enroll(db, p, "cap-test-seq-2", now - timedelta(minutes=1))
        db.commit()

        dispatcher = _make_dispatcher(db)
        result = run_scheduler(now, db, dispatcher, user_id=user.id)

        assert result.steps_executed == 2, (
            f"expected 2 executed (5 cap - 3 pre-existing), got {result.steps_executed}"
        )
        assert result.steps_deferred == 8, (
            f"expected 8 deferred, got {result.steps_deferred}"
        )
    finally:
        db.close()
