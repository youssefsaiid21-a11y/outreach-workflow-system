"""
AI generation endpoints: single email, bulk email, single sequence, bulk
sequence, manual sequence-step save.

P4-1: extracted from the old monolith.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from ...db.database import get_db
from ...db.models import ConferenceLead, User
from ..auth import get_current_user
from ._shared import (
    _is_valid_step_name,
    BulkGenerateRequest,
    GenerateRequest,
    SaveSequenceEmailRequest,
    _assert_not_replied,
    _check_ownership,
    _inflight_lock,
    _release_inflight_lock,
    _lead_to_dict,
)
from .service import _generate_email, _generate_sequence

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conference"])


@router.post("/generate")
def generate_email(req: GenerateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Generate a personalised email for one lead given a context."""
    lead = db.query(ConferenceLead).filter_by(id=req.lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user, db)
    _assert_not_replied(lead)  # Events-reply-cutoff 2026-04-09

    # FIX-19: refuse parallel generation for the same lead from a second
    # request while the first is still talking to Claude.
    lock = _inflight_lock(lead.id, "generate")
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Generation already in progress for this lead.")
    try:
        _sender = current_user.name if current_user else ""
        _uid = current_user.id if current_user else None
        subject, body, inp_tok, out_tok = _generate_email(
            lead, req.context, db, sender_name=_sender, user_id=_uid,
        )
        lead.generated_subject = subject
        lead.generated_body = body
        lead.generated_at = datetime.now(timezone.utc)
        # FIX-19: atomic increment via SQL UPDATE  -  avoids the read-modify-write
        # race when two requests run on different sessions.
        db.execute(
            update(ConferenceLead)
            .where(ConferenceLead.id == lead.id)
            .values(
                tokens_input=func.coalesce(ConferenceLead.tokens_input, 0) + inp_tok,
                tokens_output=func.coalesce(ConferenceLead.tokens_output, 0) + out_tok,
            )
        )
        db.commit()
        db.refresh(lead)
        result = _lead_to_dict(lead)
        result["_tokens"] = {"input": inp_tok, "output": out_tok}
        return result
    finally:
        _release_inflight_lock(lead.id, "generate", lock)


@router.post("/generate-all")
def generate_all(req: BulkGenerateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Generate emails for multiple leads at once."""
    _sender = current_user.name if current_user else ""
    _uid = current_user.id if current_user else None
    # Append booking link to context if enabled
    _context = req.context
    if req.include_booking_link:
        from ..settings import _get_user_setting, CALENDLY_LINK_KEY
        _cal = _get_user_setting(db, CALENDLY_LINK_KEY, _uid)
        if _cal and _cal.strip():
            _context += f"\n\nBooking link: Include this line at the end of the email, on its own after a blank line: Feel free to book a meeting here: {_cal.strip()}"
    done, failed = 0, 0
    total_inp, total_out = 0, 0
    errors = []
    for lead_id in req.lead_ids:
        lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
        if not lead:
            continue
        _check_ownership(lead, current_user, db)
        # Events-reply-cutoff 2026-04-09: skip replied leads silently in bulk ops.
        if lead.replied_at is not None:
            continue
        # FIX-19: per-lead in-flight lock
        lock = _inflight_lock(lead.id, "generate")
        if not lock.acquire(blocking=False):
            failed += 1
            errors.append({"email": lead.email, "error": "Generation already in progress for this lead."})
            continue
        try:
            subject, body, inp_tok, out_tok = _generate_email(
                lead, _context, db, sender_name=_sender, user_id=_uid,
            )
            lead.generated_subject = subject
            lead.generated_body = body
            lead.generated_at = datetime.now(timezone.utc)
            db.execute(
                update(ConferenceLead)
                .where(ConferenceLead.id == lead.id)
                .values(
                    tokens_input=func.coalesce(ConferenceLead.tokens_input, 0) + inp_tok,
                    tokens_output=func.coalesce(ConferenceLead.tokens_output, 0) + out_tok,
                )
            )
            total_inp += inp_tok
            total_out += out_tok
            db.commit()
            done += 1
        except Exception as e:
            logger.warning(f"Generation failed for {lead.email}: {e}", exc_info=True)
            errors.append({"email": lead.email, "error": "Generation failed. See server logs."})
            failed += 1
        finally:
            _release_inflight_lock(lead.id, "generate", lock)
    return {"generated": done, "failed": failed, "errors": errors, "tokens": {"input": total_inp, "output": total_out}}


@router.post("/generate-sequence")
def generate_sequence_endpoint(req: GenerateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Generate a full follow-up email sequence for one lead."""
    lead = db.query(ConferenceLead).filter_by(id=req.lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user, db)
    _assert_not_replied(lead)  # Events-reply-cutoff 2026-04-09

    # FIX-19: in-flight lock + atomic token increment.
    lock = _inflight_lock(lead.id, "generate")
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Sequence generation already in progress for this lead.")
    try:
        _sender = current_user.name if current_user else ""
        _uid = current_user.id if current_user else None
        seq = _generate_sequence(
            lead, req.context, db, sender_name=_sender, user_id=_uid,
        )
        seq_tokens = seq.pop("_tokens", {})
        inp_delta = seq_tokens.get("input", 0)
        out_delta = seq_tokens.get("output", 0)
        lead.generated_sequence = json.dumps(seq)
        lead.sequence_generated_at = datetime.now(timezone.utc)
        if seq.get("email1"):
            lead.generated_subject = seq["email1"].get("subject", "")
            lead.generated_body = seq["email1"].get("body", "")
            lead.generated_at = datetime.now(timezone.utc)
        db.execute(
            update(ConferenceLead)
            .where(ConferenceLead.id == lead.id)
            .values(
                tokens_input=func.coalesce(ConferenceLead.tokens_input, 0) + inp_delta,
                tokens_output=func.coalesce(ConferenceLead.tokens_output, 0) + out_delta,
            )
        )
        db.commit()
        db.refresh(lead)
        return _lead_to_dict(lead)
    finally:
        _release_inflight_lock(lead.id, "generate", lock)


@router.post("/generate-sequence-all")
def generate_sequence_all(req: BulkGenerateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Generate email sequences for multiple leads at once."""
    _sender = current_user.name if current_user else ""
    _uid = current_user.id if current_user else None
    # Append booking link to context if enabled
    _seq_context = req.context
    if req.include_booking_link:
        from ..settings import _get_user_setting, CALENDLY_LINK_KEY
        _cal = _get_user_setting(db, CALENDLY_LINK_KEY, _uid)
        if _cal and _cal.strip():
            _seq_context += f"\n\nBooking link: Include this line at the end of email 1, on its own after a blank line: Feel free to book a meeting here: {_cal.strip()}"
    done, failed = 0, 0
    total_inp, total_out = 0, 0
    errors = []
    for lead_id in req.lead_ids:
        lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
        if not lead:
            continue
        _check_ownership(lead, current_user, db)
        lock = _inflight_lock(lead.id, "generate")
        if not lock.acquire(blocking=False):
            failed += 1
            errors.append({"email": lead.email, "error": "Sequence generation already in progress."})
            continue
        try:
            seq = _generate_sequence(
                lead, _seq_context, db, sender_name=_sender, user_id=_uid,
            )
            seq_tok = seq.pop("_tokens", {})
            inp_delta = seq_tok.get("input", 0)
            out_delta = seq_tok.get("output", 0)
            total_inp += inp_delta
            total_out += out_delta
            lead.generated_sequence = json.dumps(seq)
            lead.sequence_generated_at = datetime.now(timezone.utc)
            if seq.get("email1"):
                lead.generated_subject = seq["email1"].get("subject", "")
                lead.generated_body = seq["email1"].get("body", "")
                lead.generated_at = datetime.now(timezone.utc)
            db.execute(
                update(ConferenceLead)
                .where(ConferenceLead.id == lead.id)
                .values(
                    tokens_input=func.coalesce(ConferenceLead.tokens_input, 0) + inp_delta,
                    tokens_output=func.coalesce(ConferenceLead.tokens_output, 0) + out_delta,
                )
            )
            db.commit()
            done += 1
        except Exception as e:
            logger.warning(f"Sequence generation failed for {lead.email}: {e}", exc_info=True)
            errors.append({"email": lead.email, "error": "Sequence generation failed. See server logs."})
            failed += 1
        finally:
            _release_inflight_lock(lead.id, "generate", lock)
    return {"generated": done, "failed": failed, "errors": errors, "tokens": {"input": total_inp, "output": total_out}}


@router.post("/generate-sequence-all-stream")
def generate_sequence_all_stream(req: BulkGenerateRequest, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Same as generate-sequence-all but streams progress via SSE."""
    _sender = current_user.name if current_user else ""
    _uid = current_user.id if current_user else None
    _seq_context = req.context
    if req.include_booking_link:
        from ..settings import _get_user_setting, CALENDLY_LINK_KEY
        _cal = _get_user_setting(db, CALENDLY_LINK_KEY, _uid)
        if _cal and _cal.strip():
            _seq_context += f"\n\nBooking link: Include this line at the end of email 1, on its own after a blank line: Feel free to book a meeting here: {_cal.strip()}"

    lead_ids = list(req.lead_ids)
    total = len(lead_ids)

    def _stream():
        done, failed = 0, 0
        total_inp, total_out = 0, 0
        for i, lead_id in enumerate(lead_ids):
            lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
            if not lead:
                continue
            try:
                _check_ownership(lead, current_user, db)
            except Exception:
                failed += 1
                yield f"data: {json.dumps({'i': i+1, 'total': total, 'done': done, 'failed': failed, 'name': '?', 'status': 'skip'})}\n\n"
                continue
            lock = _inflight_lock(lead.id, "generate")
            if not lock.acquire(blocking=False):
                failed += 1
                yield f"data: {json.dumps({'i': i+1, 'total': total, 'done': done, 'failed': failed, 'name': lead.first_name or lead.email, 'status': 'locked'})}\n\n"
                continue
            name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.email
            try:
                seq = _generate_sequence(lead, _seq_context, db, sender_name=_sender, user_id=_uid)
                seq_tok = seq.pop("_tokens", {})
                inp_delta = seq_tok.get("input", 0)
                out_delta = seq_tok.get("output", 0)
                total_inp += inp_delta
                total_out += out_delta
                lead.generated_sequence = json.dumps(seq)
                lead.sequence_generated_at = datetime.now(timezone.utc)
                if seq.get("email1"):
                    lead.generated_subject = seq["email1"].get("subject", "")
                    lead.generated_body = seq["email1"].get("body", "")
                    lead.generated_at = datetime.now(timezone.utc)
                db.execute(
                    update(ConferenceLead)
                    .where(ConferenceLead.id == lead.id)
                    .values(
                        tokens_input=func.coalesce(ConferenceLead.tokens_input, 0) + inp_delta,
                        tokens_output=func.coalesce(ConferenceLead.tokens_output, 0) + out_delta,
                    )
                )
                db.commit()
                done += 1
                yield f"data: {json.dumps({'i': i+1, 'total': total, 'done': done, 'failed': failed, 'name': name, 'status': 'ok'})}\n\n"
            except Exception as e:
                logger.warning(f"Sequence generation failed for {lead.email}: {e}", exc_info=True)
                failed += 1
                yield f"data: {json.dumps({'i': i+1, 'total': total, 'done': done, 'failed': failed, 'name': name, 'status': 'error'})}\n\n"
            finally:
                _release_inflight_lock(lead.id, "generate", lock)
        yield f"data: {json.dumps({'i': total, 'total': total, 'done': done, 'failed': failed, 'status': 'complete', 'tokens': {'input': total_inp, 'output': total_out}})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.patch("/leads/{lead_id}/sequence-email")
def save_sequence_email(lead_id: str, req: SaveSequenceEmailRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Save a manually edited email within a sequence."""
    if not _is_valid_step_name(req.step):
        raise HTTPException(400, f"Invalid step name '{req.step}'. Must be 'emailN', 'linkedin_connect_note', or 'linkedin_message'.")
    lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user)
    if not lead.generated_sequence:
        raise HTTPException(400, "No sequence generated yet.")

    seq = json.loads(lead.generated_sequence)
    if req.step not in seq:
        raise HTTPException(400, f"Step '{req.step}' not found in sequence.")
    # LinkedIn steps are plain strings, not {subject, body, word_count} dicts
    if req.step in ("linkedin_connect_note", "linkedin_message"):
        seq[req.step] = req.body
    else:
        seq[req.step] = {"subject": req.subject, "body": req.body, "word_count": len(req.body.split())}
    lead.generated_sequence = json.dumps(seq)
    # Keep single-email fields in sync with email1
    if req.step == "email1":
        lead.generated_subject = req.subject
        lead.generated_body = req.body
        lead.generated_at = datetime.now(timezone.utc)
    db.commit()
    return _lead_to_dict(lead)


# ── Refine text with AI feedback ─────────────────────────────────────────────


from pydantic import BaseModel as _BaseModel


class RefineRequest(_BaseModel):
    lead_id: str
    step: str           # "email1", "email2", "linkedin_connect_note", "linkedin_message"
    current_text: str   # the text as it currently appears
    feedback: str       # user's instruction, e.g. "make it shorter"
    subject: str = ""   # only for email steps


@router.post("/refine")
def refine_text(
    req: RefineRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Refine a single piece of generated text using AI, based on user feedback."""
    from ...config import MODEL_SMART
    from ._shared import get_anthropic_client

    lead = db.query(ConferenceLead).filter_by(id=req.lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user, db)

    is_linkedin = req.step.startswith("linkedin_")
    is_email = req.step.startswith("email")

    if is_email:
        prompt = f"""You wrote this email for outreach to {lead.first_name} {lead.last_name}, {lead.title} at {lead.company}:

Subject: {req.subject}
Body:
{req.current_text}

The user wants you to revise it with this feedback: "{req.feedback}"

Apply the feedback. Keep the same format. Return valid JSON only:
{{"subject": "...", "body": "..."}}"""
    elif is_linkedin:
        label = "LinkedIn connection request note" if "connect" in req.step else "LinkedIn message"
        prompt = f"""You wrote this {label} for {lead.first_name} {lead.last_name}, {lead.title} at {lead.company}:

{req.current_text}

The user wants you to revise it with this feedback: "{req.feedback}"

Apply the feedback. Return ONLY the revised text, no JSON, no quotes, no explanation."""
    else:
        raise HTTPException(400, f"Unknown step type: {req.step}")

    try:
        msg = get_anthropic_client().messages.create(
            model=MODEL_SMART,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        if not msg.content:
            raise HTTPException(502, "AI returned empty response")
        raw = msg.content[0].text.strip()

        # Update the lead's generated_sequence
        seq = lead.generated_sequence
        if isinstance(seq, str):
            seq = json.loads(seq)
        if not seq:
            seq = {}

        if is_email:
            data = json.loads(raw) if raw.startswith("{") else {"subject": req.subject, "body": raw}
            seq[req.step] = {"subject": data.get("subject", req.subject), "body": data.get("body", raw)}
            result = seq[req.step]
        else:
            # LinkedIn: Claude returns plain text
            cleaned = raw.strip('"').strip("'").strip()
            seq[req.step] = cleaned
            result = {"text": cleaned}

        lead.generated_sequence = json.dumps(seq) if isinstance(lead.generated_sequence, str) else seq
        if req.step == "email1":
            lead.generated_subject = seq["email1"].get("subject", "")
            lead.generated_body = seq["email1"].get("body", "")
        db.commit()

        return {"step": req.step, "result": result, "lead": _lead_to_dict(lead)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("refine failed: %s", e, exc_info=True)
        raise HTTPException(502, f"AI refinement failed: {e}")


class ApplyStyleRequest(_BaseModel):
    reference_lead_id: str
    step: str               # which step's style to replicate
    conference_name: str
    context: str = ""


@router.post("/apply-style")
def apply_style_to_all(
    req: ApplyStyleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-generate one step for all leads using a reference lead's output as the style example."""
    from ...config import MODEL_SMART
    from ._shared import get_anthropic_client

    ref_lead = db.query(ConferenceLead).filter_by(id=req.reference_lead_id).first()
    if not ref_lead:
        raise HTTPException(404, "Reference lead not found")
    _check_ownership(ref_lead, current_user, db)

    ref_seq = ref_lead.generated_sequence
    if isinstance(ref_seq, str):
        ref_seq = json.loads(ref_seq)
    if not ref_seq:
        raise HTTPException(400, "Reference lead has no generated sequence")

    # Extract reference text
    is_email = req.step.startswith("email")
    if is_email:
        ref_data = ref_seq.get(req.step, {})
        ref_text = f"Subject: {ref_data.get('subject', '')}\nBody: {ref_data.get('body', '')}"
    else:
        ref_text = ref_seq.get(req.step, "")
    if not ref_text.strip():
        raise HTTPException(400, f"Reference lead has no content for {req.step}")

    # Get all other leads for this conference
    q = db.query(ConferenceLead).filter(
        ConferenceLead.conference_name == req.conference_name,
        ConferenceLead.outreach_type == "pre",
        ConferenceLead.id != req.reference_lead_id,
        ConferenceLead.generated_sequence.isnot(None),
    )
    if current_user:
        q = q.filter(ConferenceLead.user_id == current_user.id)
    leads = q.all()

    updated = 0
    failed = 0
    for lead in leads:
        try:
            seq = lead.generated_sequence
            if isinstance(seq, str):
                seq = json.loads(seq)
            if not seq:
                continue

            if is_email:
                prompt = f"""Here is a reference outreach email that the user approved:

{ref_text}

Now write a similar email for a different person:
Name: {lead.first_name} {lead.last_name}
Title: {lead.title or ''}
Company: {lead.company or ''}

Match the same tone, length, structure, and style exactly. Only change the personalization.
Context: {req.context}

Return valid JSON only:
{{"subject": "...", "body": "..."}}"""
            else:
                label = "LinkedIn connection note" if "connect" in req.step else "LinkedIn message"
                prompt = f"""Here is a reference {label} that the user approved:

"{ref_text}"

Now write a similar {label} for a different person:
Name: {lead.first_name} {lead.last_name}
Title: {lead.title or ''}
Company: {lead.company or ''}

Match the same tone, length, and style exactly. Only change the personalization.
Return ONLY the text, no JSON, no quotes."""

            msg = get_anthropic_client().messages.create(
                model=MODEL_SMART,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            if not msg.content:
                failed += 1
                continue
            raw = msg.content[0].text.strip()

            if is_email:
                try:
                    data = json.loads(raw) if raw.startswith("{") else {"subject": "", "body": raw}
                except json.JSONDecodeError:
                    data = {"subject": "", "body": raw}
                seq[req.step] = {"subject": data.get("subject", ""), "body": data.get("body", raw)}
                if req.step == "email1":
                    lead.generated_subject = seq["email1"].get("subject", "")
                    lead.generated_body = seq["email1"].get("body", "")
            else:
                seq[req.step] = raw.strip('"').strip("'").strip()

            lead.generated_sequence = json.dumps(seq) if isinstance(lead.generated_sequence, str) else seq
            updated += 1
        except Exception as exc:
            logger.warning("apply-style failed for lead %s: %s", lead.id, exc)
            failed += 1

    db.commit()
    return {"updated": updated, "failed": failed, "total": len(leads)}
