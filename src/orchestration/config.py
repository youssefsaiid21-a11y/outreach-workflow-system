"""
Sequence configuration registry and built-in outreach sequences.

This file is the single source of truth for all outreach sequence definitions.
It serves three purposes:

  1. Defines built-in sequences (EXAMPLE_DEFAULT_SEQUENCE, EXAMPLE_LINKEDIN_ONLY_SEQUENCE)
  2. Maintains an in-memory registry (_REGISTRY) that the scheduler and dispatcher
     query via get_sequence_config(sequence_id)
  3. Loads custom sequences from the database at startup via load_custom_config_from_db()

When a campaign is launched, each enrolled lead gets an orch_sequence_state row
with a sequence_id that points to a config in this registry. The scheduler reads
the config to know what step comes next and when.

The default 30-day sequence (legacy, mostly email):

  Day  1  -  Email 1: Problem intro
  Day  4  -  LinkedIn connect (conditional)
  Day  7  -  Email 2: Follow-up insight
  Day 12  -  Email 3: Soft case study
  Day 16  -  LinkedIn message (conditional on being connected) OR manual task fallback
  Day 18  -  Email 4: Pattern break
  Day 24  -  Email 5: Light nudge
  Day 30  -  Email 6: Break-up
  Day 31  -  End sequence

The LinkedIn-only sequence (current production default):

  Day 3  -  LinkedIn connect request
  Day 6  -  LinkedIn message (if connected)

Step IDs are deterministic uuid5 values so they survive server restarts and
idempotency checks always match (see _stable_id helper).
"""

import uuid

from .enums import StepType
from .models import SequenceConfig, SequenceStep, StepCondition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stable_id(step_type: StepType, day: int, suffix: str = "") -> str:
    """
    Return a stable UUID5 for a sequence step.

    Using uuid.NAMESPACE_DNS as the namespace ensures the output is a valid
    UUID in the standard format without requiring a custom namespace.
    """
    seed = f"invigilo-default-{step_type.value}-day{day}{suffix}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


# ---------------------------------------------------------------------------
# Default Example sequence
# ---------------------------------------------------------------------------

EXAMPLE_DEFAULT_SEQUENCE = SequenceConfig(
    sequence_id="invigilo-default-v1",
    name="Example 30-Day HSE Outreach",
    timezone="Asia/Riyadh",
    scoring_threshold=5,
    business_days_only=False,
    steps=[
        # ── Day 1: First email ────────────────────────────────────────────
        SequenceStep(
            step_id=_stable_id(StepType.send_email, 1),
            step_type=StepType.send_email,
            day_offset=1,
            template_id="email_1_problem_intro",
            conditions=[],
        ),
        # ── Day 4: LinkedIn connect ───────────────────────────────────────
        # Only send if: no reply yet, not unsubscribed, and we have a URL.
        SequenceStep(
            step_id=_stable_id(StepType.send_linkedin_connect, 4),
            step_type=StepType.send_linkedin_connect,
            day_offset=4,
            template_id=None,
            note_template_id="linkedin_connect_note_v1",
            conditions=[
                StepCondition(condition_type="no_reply", value=None),
                StepCondition(condition_type="no_unsubscribe", value=None),
                StepCondition(condition_type="linkedin_url_exists", value=None),
            ],
        ),
        # ── Day 7: Follow-up email ────────────────────────────────────────
        SequenceStep(
            step_id=_stable_id(StepType.send_email, 7),
            step_type=StepType.send_email,
            day_offset=7,
            template_id="email_2_followup_insight",
            conditions=[],
        ),
        # ── Day 12: Soft case study email ────────────────────────────────
        SequenceStep(
            step_id=_stable_id(StepType.send_email, 12),
            step_type=StepType.send_email,
            day_offset=12,
            template_id="email_3_soft_case",
            conditions=[],
        ),
        # ── Day 16: LinkedIn message (falls back to manual task in handler)
        SequenceStep(
            step_id=_stable_id(StepType.send_linkedin_message, 16),
            step_type=StepType.send_linkedin_message,
            day_offset=16,
            template_id="linkedin_message_v1",
            conditions=[
                StepCondition(condition_type="linkedin_connected", value=None),
            ],
        ),
        # ── Day 18: Pattern-break email ───────────────────────────────────
        SequenceStep(
            step_id=_stable_id(StepType.send_email, 18),
            step_type=StepType.send_email,
            day_offset=18,
            template_id="email_4_pattern_break",
            conditions=[],
        ),
        # ── Day 24: Light nudge email ─────────────────────────────────────
        SequenceStep(
            step_id=_stable_id(StepType.send_email, 24),
            step_type=StepType.send_email,
            day_offset=24,
            template_id="email_5_light_nudge",
            conditions=[],
        ),
        # ── Day 30: Break-up email ────────────────────────────────────────
        SequenceStep(
            step_id=_stable_id(StepType.send_email, 30),
            step_type=StepType.send_email,
            day_offset=30,
            template_id="email_6_breakup",
            conditions=[],
        ),
        # ── Day 31: End sequence (fires day after break-up email) ──────────
        SequenceStep(
            step_id=_stable_id(StepType.end_sequence, 31),
            step_type=StepType.end_sequence,
            day_offset=31,
            template_id=None,
            conditions=[],
        ),
    ],
)

# ---------------------------------------------------------------------------
# LinkedIn-only sequence (2026-04-10, updated 2026-06-02)
# ---------------------------------------------------------------------------
#
# All campaign outreach now runs through PhantomBuster (LinkedIn connect +
# message). Conference/event emails use own Outlook mailboxes via the Graph
# API directly — not the orchestration scheduler.
#
# Day offsets match the defaults from the UI sequence builder
# (``dashboard/main.js`` ``defaultGaps = {..., send_linkedin_connect: 3, send_linkedin_message: 4}``):
# connect ~3 days after launch, message ~3 days after connect.
EXAMPLE_LINKEDIN_ONLY_SEQUENCE = SequenceConfig(
    sequence_id="invigilo-linkedin-only-v1",
    name="Example LinkedIn outreach sequence",
    timezone="Asia/Riyadh",
    scoring_threshold=5,
    business_days_only=False,
    steps=[
        SequenceStep(
            step_id=_stable_id(StepType.send_linkedin_connect, 3, suffix="-li-only"),
            step_type=StepType.send_linkedin_connect,
            day_offset=3,
            template_id=None,
            note_template_id="linkedin_connect_note_v1",
            conditions=[
                StepCondition(condition_type="no_reply", value=None),
                StepCondition(condition_type="no_unsubscribe", value=None),
                StepCondition(condition_type="linkedin_url_exists", value=None),
            ],
        ),
        SequenceStep(
            step_id=_stable_id(StepType.send_linkedin_message, 6, suffix="-li-only"),
            step_type=StepType.send_linkedin_message,
            day_offset=6,
            template_id="linkedin_message_v1",
            conditions=[
                StepCondition(condition_type="linkedin_connected", value=None),
                StepCondition(condition_type="no_reply", value=None),
                StepCondition(condition_type="no_unsubscribe", value=None),
            ],
        ),
    ],
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# The registry maps sequence_id strings to SequenceConfig objects. The scheduler
# looks up configs here on every tick. If a lead's sequence_id isn't found,
# the scheduler pauses the state to prevent error-looping.
_REGISTRY: dict[str, SequenceConfig] = {
    EXAMPLE_DEFAULT_SEQUENCE.sequence_id: EXAMPLE_DEFAULT_SEQUENCE,
    # Friendly alias so callers can use either ID
    "invigilo-default-30day": EXAMPLE_DEFAULT_SEQUENCE,
    EXAMPLE_LINKEDIN_ONLY_SEQUENCE.sequence_id: EXAMPLE_LINKEDIN_ONLY_SEQUENCE,
}


def get_sequence_config(sequence_id: str) -> SequenceConfig:
    """
    Retrieve a SequenceConfig by its ID from the in-memory registry.

    Raises:
        KeyError: If sequence_id is not registered.
    """
    try:
        return _REGISTRY[sequence_id]
    except KeyError:
        raise KeyError(f"Sequence '{sequence_id}' is not registered.  "
                       f"Available: {list(_REGISTRY.keys())}")


def register_sequence_config(config: SequenceConfig) -> None:
    """
    Register a new SequenceConfig in the in-memory registry.

    Overwrites any existing config with the same sequence_id.  This is
    intentional  -  callers can hot-reload configs during development.

    Args:
        config: The SequenceConfig to register.
    """
    _REGISTRY[config.sequence_id] = config


def load_custom_config_from_db() -> None:
    """
    Register all persisted custom sequence configs into the in-memory
    registry so the scheduler's ``get_sequence_config`` lookups resolve
    after a worker restart.

    Two sources:

    1. Legacy single-row ``app_settings.custom_sequence_config``  -
       the pre-0009 global config that every in-flight campaign was
       silently inheriting. Still used as a fallback at launch time
       when a campaign's ``sequence_config_id`` is NULL, so it must
       stay registered.

    2. The ``sequence_configs`` table (0009 migration onwards)  -
       multi-row, per-user, optionally shared. Every row's
       ``config_json`` is a serialised SequenceConfig whose
       ``sequence_id`` is the key the scheduler uses. Without this
       load, launches that pin a sequence_config_id end up with
       orch_sequence_states rows whose sequence_id is unknown to the
       scheduler, and every scheduler tick errors out with
       ``Unknown sequence_id`` until the owner edits the sequence
       (which triggers register_sequence_config at runtime).

    Safe to call at startup. Failures on individual rows log a warning
    and the loop continues  -  one corrupt row shouldn't block the rest.
    """
    import json
    import logging

    from ..db.database import SessionLocal
    from ..db.models import AppSetting, SequenceConfigORM

    logger = logging.getLogger(__name__)

    db = None
    try:
        db = SessionLocal()

        # 1. Legacy global config.
        row = db.query(AppSetting).filter_by(key="custom_sequence_config").first()
        if row and row.value:
            try:
                data = json.loads(row.value)
                config = SequenceConfig(**data)
                register_sequence_config(config)
                logger.info("Loaded legacy custom_sequence_config '%s' from app_settings", config.sequence_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not parse legacy custom_sequence_config: %s", exc)

        # 2. Multi-sequence table (0009+).
        loaded = 0
        failed = 0
        for sc_row in db.query(SequenceConfigORM).all():
            try:
                raw = sc_row.config_json
                if isinstance(raw, str):
                    raw = json.loads(raw)
                if not isinstance(raw, dict):
                    raise ValueError(f"config_json is not a dict: {type(raw).__name__}")
                config = SequenceConfig(**raw)
                register_sequence_config(config)
                loaded += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning(
                    "Could not load sequence_configs row id=%s name=%r: %s",
                    sc_row.id, sc_row.name, exc,
                )
        if loaded or failed:
            logger.info(
                "Loaded %d sequence_configs from DB (%d failed)",
                loaded, failed,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load custom sequence configs from DB: %s", exc)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
