"""
Conference reply management endpoints.

Events-reply-cutoff 2026-04-09  -  two small endpoints that sit alongside
the existing draft/generate routes:

- ``POST /conference/check-replies``
    Forces an immediate Outlook inbox poll for the currently logged-in
    user. Used by the dashboard "Check replies now" button so operators
    don't have to wait for the 10-minute background interval. Returns
    ``{"marked": N}`` so the UI can toast "Marked N leads as replied".

- ``POST /conference/leads/{lead_id}/clear-reply``
    False-positive undo. Clears ``replied_at`` + the reply metadata on a
    single lead so drafting can resume. Used when an operator sees the
    "reply" was actually an out-of-office or an unrelated email that
    happened to land in the same window. Ownership-checked.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db.database import get_db
from ...db.models import ConferenceLead, User
from ..auth import get_current_user
from ._shared import _check_ownership, _lead_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conference"])


@router.post("/check-replies")
def check_replies_now(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger an immediate Outlook inbox poll for the current user.

    Used by the dashboard "Check replies now" button so operators can
    see reply state updated in <5s instead of waiting up to 10 minutes
    for the background poller tick.

    Returns ``{"marked": N}`` where N is the number of leads newly
    marked as replied by this poll. N can be 0 (nothing new), positive
    (real replies detected), or the endpoint can raise:
      - 400 if Outlook is not connected for this user
      - 502 if Graph is unreachable / throttling

    The endpoint reuses ``poll_replies_for_user`` from the poller
    module so a manual check and the background loop share the exact
    same match logic  -  no split-brain between "live" and "polled"
    detection paths.
    """
    if current_user is None:
        # Fail-closed: the conference flow is per-user by design.
        raise HTTPException(401, "Authentication required.")

    # Import inside the function to keep the import graph clean: the
    # poller module pulls in the OutlookClient which we don't want to
    # load at app import time on a fresh install that hasn't run the
    # first deploy yet.
    from ...services.conference_reply_poller import poll_replies_for_user

    try:
        marked = poll_replies_for_user(db, current_user)
    except ValueError as e:
        # OutlookClient raises ValueError for "not connected" / "no MICROSOFT_CLIENT_ID"
        logger.warning("check_replies_now: %s", e)
        raise HTTPException(400, "Bad request. Check server logs for details.")
    except RuntimeError as e:
        # Token expired / Graph auth failure.
        logger.warning("check_replies_now: %s", e)
        raise HTTPException(400, "Bad request. Check server logs for details.")
    except Exception as e:  # noqa: BLE001
        logger.error("check_replies_now: unexpected error: %s", e, exc_info=True)
        raise HTTPException(502, "Failed to poll Outlook inbox. Check logs.")

    return {"marked": marked}


@router.post("/leads/{lead_id}/clear-reply")
def clear_reply(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear the reply flag on a single lead so drafting can resume.

    Used when an operator sees that the "reply" we detected was actually
    an out-of-office notification, an unrelated email from the same
    address, or some other false positive. After clearing, the lead is
    eligible for further sequence steps via the draft endpoints.

    Note: if the prospect's actual reply is still sitting in the user's
    inbox, the next poller tick will re-mark the lead. To make the
    clear "stick", the user also needs to move/archive the reply from
    their Outlook inbox.

    Returns the updated lead dict.
    """
    lead = db.query(ConferenceLead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    _check_ownership(lead, current_user, db)

    if lead.replied_at is None:
        # Idempotent: clearing a lead that isn't flagged is a no-op.
        return _lead_to_dict(lead)

    logger.info(
        "clear_reply: user=%s lead=%s previously replied_at=%s",
        current_user.email if current_user else "?",
        lead.email,
        lead.replied_at,
    )
    lead.replied_at = None
    lead.reply_subject = None
    lead.reply_snippet = None
    lead.reply_from_email = None
    db.commit()
    db.refresh(lead)
    return _lead_to_dict(lead)
