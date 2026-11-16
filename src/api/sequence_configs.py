"""
Sequence configs API  -  multi-sequence CRUD for the orchestration step
structure.

This is distinct from ``sequence_templates`` (which snapshots rendered
email *bodies* for cloning). A "sequence config" stores the orchestration
*structure*  -  step types, day_offsets, conditions, timezone  -  i.e. the
thing the orchestration scheduler reads to decide when to fire which
action. Operators can save several named configs, optionally share them
with the workspace, and pin each campaign to a specific one at creation
time via ``Campaign.sequence_config_id``.

Before this module existed, there was a single global ``app_settings``
row keyed ``custom_sequence_config`` holding ONE config. Every save
overwrote it and campaigns silently inherited whatever was current at
launch time. The legacy row + endpoint still work as a fallback for
campaigns whose ``sequence_config_id`` is NULL  -  see
``_launch_campaign_locked`` and ``_run_generate_job`` for the resolution
order.

Scope rules:
- Per-user ownership via ``owner_user_id``. Only the owner can rename,
  edit steps, toggle sharing, or delete.
- Any user can LIST configs owned by them OR any shared config.
- Any user can GET a shared config (read-only).
- Delete refuses while any non-archived/cancelled campaign references
  the config  -  otherwise in-flight campaigns lose their structure.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import Campaign, CampaignStatus, SequenceConfigORM, User
from .auth import get_current_user

router = APIRouter(prefix="/sequence-configs", tags=["sequence-configs"])
logger = logging.getLogger(__name__)


# ── Schemas ──────────────────────────────────────────────────────────────────


class _RawStep(BaseModel):
    """One raw step as sent from the dashboard sequence designer."""
    step_type: str
    day_offset: int = 0
    template_id: Optional[str] = None
    label: Optional[str] = None


class SequenceConfigCreateRequest(BaseModel):
    name: str
    steps: list[_RawStep]
    timezone: str = "Asia/Riyadh"
    business_days_only: bool = False
    scoring_threshold: int = 5
    is_shared: bool = False


class SequenceConfigPatchRequest(BaseModel):
    name: Optional[str] = None
    steps: Optional[list[_RawStep]] = None
    timezone: Optional[str] = None
    business_days_only: Optional[bool] = None
    scoring_threshold: Optional[int] = None
    is_shared: Optional[bool] = None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_sequence_config(
    *,
    name: str,
    raw_steps: list[_RawStep],
    timezone: str,
    business_days_only: bool,
    scoring_threshold: int,
    sequence_id: str,
):
    """
    Turn a raw step list from the UI into a validated ``SequenceConfig``
    pydantic object  -  including the terminal ``end_sequence`` step and
    auto-added LinkedIn conditions.

    Mirrors the builder in ``src/api/settings.py::save_sequence_config``
    so both entry points produce an identical structure. Duplicated here
    so the legacy endpoint can stay untouched and we don't couple the
    new module to the deprecated one.
    """
    from ..orchestration.enums import StepType
    from ..orchestration.models import SequenceConfig, SequenceStep, StepCondition

    LINKEDIN_STEP_TYPES = {
        StepType.send_linkedin_connect,
        StepType.send_linkedin_message,
    }

    built_steps: list[SequenceStep] = []
    max_day = 0

    for raw in raw_steps:
        try:
            step_type = StepType(raw.step_type)
        except ValueError:
            valid = [e.value for e in StepType if e != StepType.end_sequence]
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid step_type '{raw.step_type}'. "
                    f"Valid types: {', '.join(valid)}"
                ),
            )

        if step_type == StepType.send_email:
            raise HTTPException(
                status_code=422,
                detail="Email steps are no longer supported. Use LinkedIn steps only.",
            )

        if step_type == StepType.end_sequence:
            # The terminal end_sequence step is always appended
            # automatically  -  operators should never pass it explicitly.
            raise HTTPException(
                status_code=422,
                detail=(
                    "'end_sequence' is appended automatically. Remove it "
                    "from the steps list."
                ),
            )

        day_offset = int(raw.day_offset or 0)
        if day_offset < 0:
            raise HTTPException(
                status_code=422,
                detail=f"Step '{raw.step_type}' has negative day_offset ({day_offset}).",
            )
        max_day = max(max_day, day_offset)

        # Deterministic step_id keyed off the sequence_id + step type +
        # day_offset. Same seed → same id → idempotent re-saves.
        seed = f"{sequence_id}-{step_type.value}-day{day_offset}"
        step_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))

        conditions: list[StepCondition] = []
        if step_type in LINKEDIN_STEP_TYPES:
            conditions.append(StepCondition(condition_type="no_reply", value=None))
            conditions.append(StepCondition(condition_type="no_unsubscribe", value=None))
            conditions.append(StepCondition(condition_type="linkedin_url_exists", value=None))
        if step_type == StepType.send_linkedin_message:
            conditions.append(StepCondition(condition_type="linkedin_connected", value=None))

        built_steps.append(SequenceStep(
            step_id=step_id,
            step_type=step_type,
            day_offset=day_offset,
            template_id=raw.template_id,
            conditions=conditions,
            label=raw.label,
        ))

    if not built_steps:
        raise HTTPException(
            status_code=422,
            detail="Sequence must have at least one step.",
        )

    # Terminal end_sequence step.
    end_day = max_day + 1
    end_seed = f"{sequence_id}-end_sequence-day{end_day}"
    built_steps.append(SequenceStep(
        step_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, end_seed)),
        step_type=StepType.end_sequence,
        day_offset=end_day,
        template_id=None,
        conditions=[],
    ))

    return SequenceConfig(
        sequence_id=sequence_id,
        name=name,
        steps=built_steps,
        scoring_threshold=scoring_threshold,
        business_days_only=business_days_only,
        timezone=timezone,
    )


def _serialize(row: SequenceConfigORM, current_user_id: str) -> dict[str, Any]:
    """Public JSON shape for a sequence_config row."""
    config = row.config_json if isinstance(row.config_json, dict) else {}
    return {
        "id": row.id,
        "name": row.name,
        "owner_user_id": row.owner_user_id,
        "is_shared": bool(row.is_shared),
        "is_owner": row.owner_user_id == current_user_id,
        "step_count": int(row.step_count or 0),
        "config": config,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _count_non_terminal_steps(config) -> int:
    """Steps the operator actually configured, excluding end_sequence."""
    from ..orchestration.enums import StepType

    return sum(
        1 for s in (config.steps or [])
        if s.step_type != StepType.end_sequence
    )


def _persist_config_on_row(
    row: SequenceConfigORM,
    config,
) -> None:
    """Write a built SequenceConfig onto an ORM row (shared between create + patch)."""
    row.config_json = config.model_dump(mode="json")
    row.step_count = _count_non_terminal_steps(config)


def _load_config_for_user(
    db: Session, config_id: str, current_user: User,
) -> SequenceConfigORM:
    row = db.query(SequenceConfigORM).filter_by(id=config_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Sequence config not found.")
    # Read-access: owner or shared.
    if row.owner_user_id != current_user.id and not row.is_shared:
        raise HTTPException(status_code=404, detail="Sequence config not found.")
    return row


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("")
def list_sequence_configs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List configs owned by the current user OR shared by anyone."""
    rows = (
        db.query(SequenceConfigORM)
        .filter(or_(
            SequenceConfigORM.owner_user_id == current_user.id,
            SequenceConfigORM.is_shared == True,  # noqa: E712
        ))
        .order_by(
            SequenceConfigORM.updated_at.desc().nullslast(),
            SequenceConfigORM.created_at.desc(),
        )
        .all()
    )
    return [_serialize(r, current_user.id) for r in rows]


@router.post("")
def create_sequence_config(
    req: SequenceConfigCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Sequence name is required.")
    if len(name) > 200:
        raise HTTPException(status_code=422, detail="Sequence name must be 200 characters or fewer.")

    # Build a deterministic internal sequence_id from the owner + name so
    # the orch scheduler can look it up via get_sequence_config(). Two
    # reps with the same config "Default" get distinct internal ids.
    seed = f"seqcfg-{current_user.id}-{name}"
    internal_seq_id = f"sc-{uuid.uuid5(uuid.NAMESPACE_DNS, seed)}"

    config = _build_sequence_config(
        name=name,
        raw_steps=req.steps,
        timezone=req.timezone,
        business_days_only=req.business_days_only,
        scoring_threshold=req.scoring_threshold,
        sequence_id=internal_seq_id,
    )

    row = SequenceConfigORM(
        id=str(uuid.uuid4()),
        name=name,
        owner_user_id=current_user.id,
        is_shared=bool(req.is_shared),
    )
    _persist_config_on_row(row, config)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"You already have a sequence named '{name}'. Pick a different name.",
        )
    db.refresh(row)

    # Hot-register in the orchestration in-memory registry so the
    # scheduler can look it up on its next tick without waiting for a
    # process restart.
    try:
        from ..orchestration.config import register_sequence_config
        register_sequence_config(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("register_sequence_config failed for %s: %s", row.id, exc)

    return _serialize(row, current_user.id)


@router.get("/{config_id}")
def get_sequence_config(
    config_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = _load_config_for_user(db, config_id, current_user)
    return _serialize(row, current_user.id)


@router.patch("/{config_id}")
def patch_sequence_config(
    config_id: str,
    req: SequenceConfigPatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(SequenceConfigORM).filter_by(id=config_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Sequence config not found.")
    if row.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the owner can edit this sequence config.",
        )

    # Metadata-only patches (name, is_shared) don't touch the stored
    # config_json structure. Steps/timezone/etc. rebuild the whole thing.
    rebuild_needed = any(
        v is not None for v in (
            req.steps, req.timezone, req.business_days_only, req.scoring_threshold,
        )
    )

    new_name = row.name
    if req.name is not None:
        new_name = req.name.strip()
        if not new_name:
            raise HTTPException(status_code=422, detail="Name cannot be empty.")
        row.name = new_name

    if req.is_shared is not None:
        row.is_shared = bool(req.is_shared)

    if rebuild_needed:
        current = row.config_json if isinstance(row.config_json, dict) else {}
        # Fall back to the stored value for any field the patch omits.
        effective_timezone = req.timezone if req.timezone is not None else current.get("timezone", "Asia/Riyadh")
        effective_bdo = (
            req.business_days_only
            if req.business_days_only is not None
            else bool(current.get("business_days_only", False))
        )
        effective_threshold = (
            req.scoring_threshold
            if req.scoring_threshold is not None
            else int(current.get("scoring_threshold", 5))
        )

        if req.steps is None:
            # Patch didn't include steps  -  rebuild from the stored step
            # list so field updates (timezone etc.) apply cleanly.
            raw_steps: list[_RawStep] = []
            for s in current.get("steps") or []:
                st = s.get("step_type")
                if st == "end_sequence":
                    continue
                raw_steps.append(_RawStep(
                    step_type=st,
                    day_offset=int(s.get("day_offset") or 0),
                    template_id=s.get("template_id"),
                    label=s.get("label"),
                ))
        else:
            raw_steps = list(req.steps)

        if not raw_steps:
            raise HTTPException(
                status_code=422,
                detail="Sequence must have at least one step.",
            )

        # Re-use the row's existing internal sequence_id so the orch
        # registry key stays stable across edits.
        internal_seq_id = (
            current.get("sequence_id")
            or f"sc-{uuid.uuid5(uuid.NAMESPACE_DNS, f'seqcfg-{row.owner_user_id}-{new_name}')}"
        )

        config = _build_sequence_config(
            name=new_name,
            raw_steps=raw_steps,
            timezone=effective_timezone,
            business_days_only=effective_bdo,
            scoring_threshold=effective_threshold,
            sequence_id=internal_seq_id,
        )
        _persist_config_on_row(row, config)

        try:
            from ..orchestration.config import register_sequence_config
            register_sequence_config(config)
        except Exception as exc:  # noqa: BLE001
            logger.warning("register_sequence_config failed on patch for %s: %s", row.id, exc)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"You already have a sequence named '{row.name}'. Pick a different name.",
        )
    db.refresh(row)
    return _serialize(row, current_user.id)


@router.delete("/{config_id}")
def delete_sequence_config(
    config_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(SequenceConfigORM).filter_by(id=config_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Sequence config not found.")
    if row.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the owner can delete this sequence config.",
        )

    # Refuse deletion if any non-terminal campaign references it. An
    # archived or cancelled campaign is fine  -  those are read-only.
    in_use = (
        db.query(Campaign)
        .filter(Campaign.sequence_config_id == config_id)
        .filter(~Campaign.status.in_([
            CampaignStatus.archived,
            CampaignStatus.cancelled,
        ]))
        .count()
    )
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete: {in_use} non-archived campaign(s) are using "
                f"this sequence. Cancel or archive them first."
            ),
        )

    db.delete(row)
    db.commit()
    return {"deleted": True, "id": config_id}
