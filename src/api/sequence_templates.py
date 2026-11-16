"""
Sequence templates API.

CRUD + apply for the ``SequenceTemplate`` model.

Scope rules (operator decision 2026-04-09):
- Templates are per-user. ``owner_user_id`` is set to the current user on
  create and never changes.
- Owners can rename, share/unshare, and delete their templates.
- Other users can LIST and APPLY templates only when ``is_shared=True``.
- Apply clones the template's stored bodies into every ``EmailSequence`` row
  for the target campaign, replacing whatever Claude generated. Designed so
  the operator can skip the generation pass entirely when reusing a known
  good sequence.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import (
    Campaign,
    EmailSequence,
    SequenceTemplate,
    User,
)
from .auth import get_current_user

router = APIRouter(prefix="/sequence-templates", tags=["sequence-templates"])
logger = logging.getLogger(__name__)


# ── Schemas ──────────────────────────────────────────────────────────────────


class TemplateStep(BaseModel):
    type: str  # "email" | "linkedin_connect" | "linkedin_message"
    step_number: Optional[int] = None
    subject: Optional[str] = None
    body: str


class TemplateCreateRequest(BaseModel):
    name: str
    source_campaign_id: str
    source_prospect_id: Optional[str] = None  # if omitted, picks any approved sequence in the campaign
    is_shared: bool = False


class TemplatePatchRequest(BaseModel):
    name: Optional[str] = None
    is_shared: Optional[bool] = None


class TemplateOut(BaseModel):
    id: str
    name: str
    owner_user_id: str
    is_shared: bool
    step_count: int
    steps: list[TemplateStep]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_owner: bool


def _serialize(t: SequenceTemplate, current_user_id: str) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "owner_user_id": t.owner_user_id,
        "is_shared": t.is_shared,
        "step_count": t.step_count or 0,
        "steps": t.steps_json or [],
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "is_owner": t.owner_user_id == current_user_id,
    }


def _seq_to_steps(seq: EmailSequence) -> list[dict[str, Any]]:
    """Snapshot a generated EmailSequence row into the template steps_json shape (LinkedIn-only)."""
    steps: list[dict[str, Any]] = []
    if seq.linkedin_connect_note:
        steps.append({"type": "linkedin_connect", "step_number": None, "subject": None, "body": seq.linkedin_connect_note})
    if seq.linkedin_message:
        steps.append({"type": "linkedin_message", "step_number": None, "subject": None, "body": seq.linkedin_message})
    return steps


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("")
def list_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List templates owned by the current user OR shared by anyone."""
    rows = (
        db.query(SequenceTemplate)
        .filter(or_(
            SequenceTemplate.owner_user_id == current_user.id,
            SequenceTemplate.is_shared == True,  # noqa: E712
        ))
        .order_by(SequenceTemplate.updated_at.desc().nullslast(), SequenceTemplate.created_at.desc())
        .all()
    )
    return [_serialize(t, current_user.id) for t in rows]


@router.post("")
def create_template(
    req: TemplateCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Snapshot an existing approved sequence into a reusable template."""
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Template name is required.")

    # Ownership check on the source campaign  -  only the user who owns it can
    # snapshot from it. Shared templates are created by the original owner.
    campaign = db.query(Campaign).filter_by(id=req.source_campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Source campaign not found.")
    if campaign.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only template your own campaigns.")

    seq_q = db.query(EmailSequence).filter(EmailSequence.campaign_id == req.source_campaign_id)
    if req.source_prospect_id:
        seq_q = seq_q.filter(EmailSequence.prospect_id == req.source_prospect_id)
    else:
        seq_q = seq_q.filter(EmailSequence.approved == True)  # noqa: E712
    seq = seq_q.first()
    if not seq:
        raise HTTPException(
            status_code=404,
            detail="No source sequence found. Approve at least one sequence in this campaign first.",
        )

    steps = _seq_to_steps(seq)
    if not steps:
        raise HTTPException(
            status_code=422,
            detail="Source sequence is empty  -  nothing to template.",
        )

    template = SequenceTemplate(
        id=str(uuid.uuid4()),
        name=name,
        owner_user_id=current_user.id,
        is_shared=bool(req.is_shared),
        steps_json=steps,
        step_count=len(steps),
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return _serialize(template, current_user.id)


@router.patch("/{template_id}")
def patch_template(
    template_id: str,
    req: TemplatePatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    template = db.query(SequenceTemplate).filter_by(id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found.")
    if template.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can edit this template.")
    if req.name is not None:
        new_name = req.name.strip()
        if not new_name:
            raise HTTPException(status_code=422, detail="Name cannot be empty.")
        template.name = new_name
    if req.is_shared is not None:
        template.is_shared = bool(req.is_shared)
    db.commit()
    db.refresh(template)
    return _serialize(template, current_user.id)


@router.delete("/{template_id}")
def delete_template(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    template = db.query(SequenceTemplate).filter_by(id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found.")
    if template.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can delete this template.")
    db.delete(template)
    db.commit()
    return {"deleted": True, "id": template_id}


@router.post("/{template_id}/apply/{campaign_id}")
def apply_template(
    template_id: str,
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Apply a template's bodies to every prospect in the target campaign,
    replacing any previously generated content.

    The dashboard branches "Generate fresh with Claude" vs "Use saved
    template X" off this  -  apply is a no-Claude clone job.
    """
    template = db.query(SequenceTemplate).filter_by(id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found.")
    if template.owner_user_id != current_user.id and not template.is_shared:
        raise HTTPException(status_code=403, detail="This template is private.")

    campaign = db.query(Campaign).filter_by(id=campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Target campaign not found.")
    if campaign.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only apply templates to your own campaigns.")

    # Build per-step value maps from the template JSON (LinkedIn-only).
    li_connect: Optional[str] = None
    li_message: Optional[str] = None
    for step in template.steps_json or []:
        st = step.get("type")
        if st == "linkedin_connect":
            li_connect = step.get("body") or None
        elif st == "linkedin_message":
            li_message = step.get("body") or None

    sequences = db.query(EmailSequence).filter(EmailSequence.campaign_id == campaign_id).all()
    if not sequences:
        raise HTTPException(
            status_code=422,
            detail="Target campaign has no prospects. Add prospects before applying a template.",
        )

    applied = 0
    for seq in sequences:
        seq.linkedin_connect_note = li_connect
        seq.linkedin_message = li_message
        # Templates land approved by default  -  they were already vetted on
        # the source campaign. Operator can still un-approve individual rows.
        seq.approved = True
        applied += 1

    db.commit()
    logger.info(
        "apply_template: user=%s template=%s campaign=%s applied=%d",
        current_user.id, template_id, campaign_id, applied,
    )
    return {
        "applied": applied,
        "template_id": template_id,
        "template_name": template.name,
        "campaign_id": campaign_id,
    }
