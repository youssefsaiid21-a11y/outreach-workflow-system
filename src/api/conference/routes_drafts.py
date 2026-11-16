"""
Draft creation endpoints: per-step sequence draft, single email draft,
clear-draft, bulk draft.

P4-1: extracted from the old monolith.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db.database import get_db
from ...db.models import ConferenceInbox, ConferenceLead, User
from ...services import draft as draft_svc
from ..auth import get_current_user
from ._shared import (
    _assert_not_replied,
    _check_ownership,
    _lead_to_dict,
    _outlook_token_file,
    _strip_greeting,
    _strip_signoff,
)
from .service import (
    _dispatch_draft,
    _get_imap_password,
    cancel_followup_reminders,
    schedule_followup_reminders,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conference"])


@router.post("/draft-sequence/{lead_id}")
def draft_sequence_step(
    lead_id: str,
    step: str = "email1",
    inbox_id: Optional[str] = None,
    cc: Optional[str] = None,
    strip_greeting: bool = False,
    strip_signoff: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Push a specific step from the sequence as a draft.

    Events-sig-fix 2026-04-09: ``strip_signoff`` removes the trailing
    closer block so Outlook's auto-signature can take over.
    2026-04-13: default changed to True  -  deterministic strip always
    runs unless caller explicitly opts out.

    Events-reply-cutoff 2026-04-09: if the lead has already replied
    (``replied_at`` set), the request is refused with 409  -  we do not
    push any further sequence steps for a lead that responded.
    """
    lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user)
    _assert_not_replied(lead)
    if not lead.generated_sequence:
        raise HTTPException(400, "No sequence generated yet.")

    seq = json.loads(lead.generated_sequence)
    email = seq.get(step)
    if not email or not isinstance(email, dict):
        raise HTTPException(400, f"Step '{step}' not found or not an email step.")

    # FIX-12: scope inbox lookup to the current user  -  otherwise a rep could
    # send a draft via another rep's IMAP credentials by passing their inbox id.
    inbox = None
    if inbox_id and inbox_id != "outlook":
        inbox_q = db.query(ConferenceInbox).filter_by(id=inbox_id)
        if current_user:
            inbox_q = inbox_q.filter(ConferenceInbox.user_id == current_user.id)
        inbox = inbox_q.first()
        if not inbox:
            raise HTTPException(404, "Inbox not found")

    to_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.email
    _sender = current_user.name if current_user else ""
    subject = email.get("subject", "")
    body = email.get("body", "")
    if strip_greeting:
        body = _strip_greeting(body)
    if strip_signoff:
        # Pass empty sender_name so the closer is fully removed (not re-appended).
        # The user's Outlook signature provides the sign-off instead.
        body = _strip_signoff(body, sender_name="")

    # Load user's Outlook signature for injection
    from ..settings import _get_user_setting, OUTLOOK_SIGNATURE_KEY
    _sig_html = _get_user_setting(db, OUTLOOK_SIGNATURE_KEY, current_user.id if current_user else None)

    # Check if this step should be a reply to email 1 (follow-up in same thread)
    _is_reply = False
    if lead.draft_message_id and lead.draft_message_id != "imap-draft" and step != "email1":
        from .service import _load_seq_steps
        try:
            step_num = int(step.replace("email", ""))
            for _idx, _day, _lim, _desc, _fu in _load_seq_steps(db):
                if _idx == step_num and _fu:
                    _is_reply = True
                    break
        except (ValueError, TypeError):
            pass

    try:
        if inbox is None or inbox.type == "outlook":
            if inbox is None or inbox.id == "outlook":
                tf = _outlook_token_file(current_user)
                if not tf.exists():
                    raise ValueError("Outlook is not connected for this user.")
            else:
                tf = Path(__file__).resolve().parent.parent.parent.parent / f"token_cache_{inbox.id}.json"
            token_file = str(tf)

            if _is_reply:
                result = draft_svc.create_reply_draft_outlook(
                    lead.draft_message_id, body, token_file=token_file,
                    to_email=lead.email, to_name=to_name,
                    signature_html=_sig_html,
                )
            else:
                # Merge lead CC + global CC
                cc_parts = []
                if lead.cc:
                    cc_parts.append(lead.cc)
                if cc:
                    cc_parts.append(cc)
                merged_cc = ", ".join(cc_parts) if cc_parts else None
                result = draft_svc.create_draft_outlook(lead.email, to_name, subject, body, token_file=token_file, cc=merged_cc, signature_html=_sig_html)
        else:
            if not inbox.imap_host or not inbox.imap_password:
                raise ValueError("IMAP inbox is missing host or password.")
            result = draft_svc.create_draft_imap(
                to_email=lead.email, to_name=to_name, subject=subject, body=body,
                from_email=inbox.email, from_name=inbox.name,
                imap_host=inbox.imap_host, imap_port=inbox.imap_port or 993,
                imap_password=_get_imap_password(inbox), use_ssl=inbox.imap_use_ssl,
            )

        # Track which steps have been drafted
        drafted_steps = seq.get("_drafted_steps", [])
        if step not in drafted_steps:
            drafted_steps.append(step)
        seq["_drafted_steps"] = drafted_steps
        lead.generated_sequence = json.dumps(seq)

        # Update the top-level draft tracking for email1
        if step == "email1":
            lead.draft_message_id = result.get("message_id") or "imap-draft"
            lead.draft_created_at = datetime.now(timezone.utc)
            # Events-reminders 2026-04-09: schedule Outlook calendar
            # reminders for every remaining sequence step so the rep gets
            # a native popup when it's time to push email 2, 3, ...
            # Best-effort  -  failures never block the draft operation.
            try:
                schedule_followup_reminders(db, lead, current_user)
            except Exception as exc:
                logger.warning(
                    "Calendar reminders for %s failed (non-fatal): %s",
                    lead.email, exc,
                )
        else:
            # Events-reminders: rep manually drafted a follow-up step
            # ahead of its scheduled reminder. Cancel that step's
            # calendar event so they don't get a stale nudge later.
            try:
                cancel_followup_reminders(lead, current_user, step_keys=[step])
            except Exception as exc:
                logger.warning(
                    "Calendar cancel for %s step %s failed (non-fatal): %s",
                    lead.email, step, exc,
                )
        db.commit()
    except ValueError as e:
        logger.warning("Sequence draft refused for %s step %s: %s", lead.email, step, e)
        raise HTTPException(400, "Draft refused. Check inputs and try again.")
    except Exception as e:
        logger.error("Sequence draft failed for %s step %s: %s", lead.email, step, e, exc_info=True)
        raise HTTPException(500, f"Draft creation failed for {step}.")

    return _lead_to_dict(lead)


@router.post("/leads/{lead_id}/clear-draft")
def clear_draft(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Reset draft status so the lead can be re-drafted."""
    lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user)
    # Events-reminders 2026-04-09: if we had scheduled calendar reminders,
    # cancel them BEFORE wiping draft state. Otherwise the rep gets stale
    # popups for a draft they explicitly cleared.
    if lead.calendar_event_ids:
        try:
            cancel_followup_reminders(lead, current_user)
        except Exception as exc:
            logger.warning(
                "Calendar cancel on clear-draft for %s failed (non-fatal): %s",
                lead.email, exc,
            )
    lead.draft_message_id = None
    lead.draft_created_at = None
    # Also clear drafted steps from sequence if present
    if lead.generated_sequence:
        try:
            seq = json.loads(lead.generated_sequence)
            seq.pop("_drafted_steps", None)
            lead.generated_sequence = json.dumps(seq)
        except (json.JSONDecodeError, TypeError):
            pass
    db.commit()
    return _lead_to_dict(lead)


@router.post("/draft/{lead_id}")
def create_draft(
    lead_id: str,
    inbox_id: Optional[str] = None,
    cc: Optional[str] = None,
    strip_greeting: bool = False,
    strip_signoff: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Push the generated email as a draft. inbox_id selects which inbox to use. cc adds CC recipients.

    Events-sig-fix 2026-04-09: ``strip_signoff`` removes the trailing
    closer block ("Talk soon,\\nName") so Outlook's auto-signature is the
    sole sign-off. The frontend UI toggle "Include closer" maps to the
    inverse of this flag.
    2026-04-13: default changed to True  -  strip always runs.
    """
    lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user)
    _assert_not_replied(lead)  # Events-reply-cutoff 2026-04-09
    if not lead.generated_subject or not lead.generated_body:
        raise HTTPException(400, "Generate an email first before creating a draft.")

    # FIX-12: scope inbox lookup by user.
    inbox = None
    if inbox_id and inbox_id != "outlook":
        inbox_q = db.query(ConferenceInbox).filter_by(id=inbox_id)
        if current_user:
            inbox_q = inbox_q.filter(ConferenceInbox.user_id == current_user.id)
        inbox = inbox_q.first()
        if not inbox:
            raise HTTPException(404, "Inbox not found")

    from ..settings import _get_user_setting, OUTLOOK_SIGNATURE_KEY
    _sig = _get_user_setting(db, OUTLOOK_SIGNATURE_KEY, current_user.id if current_user else None)
    try:
        result = _dispatch_draft(
            lead, inbox,
            current_user=current_user, extra_cc=cc,
            strip_greeting=strip_greeting, strip_signoff=strip_signoff,
            signature_html=_sig,
        )
        lead.draft_message_id = result.get("message_id") or "imap-draft"
        lead.draft_created_at = datetime.now(timezone.utc)
        # Events-reminders 2026-04-09: when generated_sequence exists the
        # single-email draft path is effectively pushing email 1 of the
        # sequence (routes_generate mirrors email1 into generated_subject/
        # body at generation time). Schedule follow-up reminders now.
        try:
            schedule_followup_reminders(db, lead, current_user)
        except Exception as exc:
            logger.warning(
                "Calendar reminders for %s failed (non-fatal): %s",
                lead.email, exc,
            )
        db.commit()
    except ValueError as e:
        # Surface operator-actionable errors (e.g. "Outlook not connected")
        # as 400 with a generic message. The real reason is in the server log.
        logger.warning("Draft creation refused for %s: %s", lead.email, e)
        raise HTTPException(400, "Draft creation refused. Check configuration and try again.")
    except Exception as e:
        logger.error("Draft creation failed for %s: %s", lead.email, e, exc_info=True)
        raise HTTPException(500, "Draft creation failed. Please try again.")

    return _lead_to_dict(lead)


@router.post("/draft-all")
def draft_all(
    conference_name: Optional[str] = None,
    inbox_id: Optional[str] = None,
    cc: Optional[str] = None,
    strip_greeting: bool = False,
    strip_signoff: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create drafts for all leads that have generated emails but no draft yet."""
    # FIX-21: never operate on the global lead set when no user context exists.
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")
    q = db.query(ConferenceLead).filter(
        ConferenceLead.generated_subject.isnot(None),
        ConferenceLead.draft_message_id.is_(None),
        # Events-reply-cutoff 2026-04-09: bulk operation must skip leads
        # that have already replied. Unlike the single-lead endpoint
        # (which raises 409 so the UI can surface a specific reason),
        # draft-all is a sweep operation  -  we silently exclude replied
        # leads from the query and report the count in the response.
        ConferenceLead.replied_at.is_(None),
    )
    if current_user:
        q = q.filter(ConferenceLead.user_id == current_user.id)
    if conference_name:
        q = q.filter_by(conference_name=conference_name)
    leads = q.all()

    if not leads:
        return {"drafted": 0, "failed": 0}

    # FIX-12: scope inbox lookup by user.
    inbox = None
    if inbox_id and inbox_id != "outlook":
        inbox_q = db.query(ConferenceInbox).filter_by(id=inbox_id)
        if current_user:
            inbox_q = inbox_q.filter(ConferenceInbox.user_id == current_user.id)
        inbox = inbox_q.first()
        if not inbox:
            raise HTTPException(404, "Inbox not found")

    from ..settings import _get_user_setting, OUTLOOK_SIGNATURE_KEY
    _sig_bulk = _get_user_setting(db, OUTLOOK_SIGNATURE_KEY, current_user.id if current_user else None)
    done, failed = 0, 0
    for lead in leads:
        try:
            result = _dispatch_draft(
                lead, inbox,
                current_user=current_user, extra_cc=cc,
                strip_greeting=strip_greeting, strip_signoff=strip_signoff,
                signature_html=_sig_bulk,
            )
            lead.draft_message_id = result.get("message_id") or "imap-draft"
            lead.draft_created_at = datetime.now(timezone.utc)
            # Events-reminders 2026-04-09: bulk push also starts the
            # sequence, so schedule follow-up reminders per lead.
            # Per-lead try/except already wraps this whole loop body.
            try:
                schedule_followup_reminders(db, lead, current_user)
            except Exception as rexc:
                logger.warning(
                    "Calendar reminders for %s failed (non-fatal): %s",
                    lead.email, rexc,
                )
            db.commit()
            done += 1
        except Exception as e:
            logger.warning(f"Draft failed for {lead.email}: {e}")
            failed += 1

    return {"drafted": done, "failed": failed}
