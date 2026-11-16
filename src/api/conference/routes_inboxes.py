"""
Inbox CRUD + connectivity test endpoints.

P4-1: extracted from the old monolith.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db.database import get_db
from ...db.models import ConferenceInbox, User
from ...services.crypto import encrypt as _encrypt, ENCRYPTION_AVAILABLE
from ..auth import get_current_user
from ._shared import InboxCreate, _check_ownership, _outlook_token_file
from .service import _get_imap_password

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conference"])


@router.get("/inboxes")
def list_inboxes(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    q = db.query(ConferenceInbox).order_by(ConferenceInbox.created_at)
    if current_user:
        q = q.filter(ConferenceInbox.user_id == current_user.id)
    inboxes = q.all()
    result = [
        {
            "id": i.id, "name": i.name, "email": i.email, "type": i.type,
            "imap_host": i.imap_host, "imap_port": i.imap_port,
            "imap_use_ssl": i.imap_use_ssl,
        }
        for i in inboxes
    ]
    return [{"id": "outlook", "name": "Personal Outlook", "email": "(Microsoft Graph)", "type": "outlook"}] + result


@router.post("/inboxes")
def add_inbox(req: InboxCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if req.type == "imap" and (not req.imap_host or not req.imap_password):
        raise HTTPException(400, "IMAP inboxes require imap_host and imap_password.")
    if req.type == "imap" and req.imap_password and not ENCRYPTION_AVAILABLE:
        raise HTTPException(
            500,
            "Cannot store IMAP password: the 'cryptography' package is not installed. "
            "Run 'pip install cryptography' and restart the server."
        )
    stored_password = req.imap_password
    if stored_password and ENCRYPTION_AVAILABLE:
        stored_password = _encrypt(stored_password)
    inbox = ConferenceInbox(
        id=str(uuid.uuid4()),
        name=req.name,
        email=req.email,
        type=req.type,
        imap_host=req.imap_host,
        imap_port=req.imap_port,
        imap_password=stored_password,
        imap_use_ssl=req.imap_use_ssl,
        user_id=current_user.id if current_user else None,
    )
    db.add(inbox)
    db.commit()
    return {"id": inbox.id, "name": inbox.name, "email": inbox.email, "type": inbox.type}


@router.post("/inboxes/{inbox_id}/test")
def test_inbox(inbox_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Test that the inbox credentials are valid."""
    if inbox_id == "outlook":
        c = None
        try:
            from ...services.outlook import OutlookClient
            # P3-3: use the authenticated user's dedicated token cache so the
            # test call never touches another rep's cache file.
            c = OutlookClient(token_file=_outlook_token_file(current_user))
            c._get_token()
            return {"ok": True, "message": "Outlook authenticated successfully."}
        except Exception as e:
            logger.error("Outlook auth failed: %s", e, exc_info=True)
            raise HTTPException(400, "Outlook authentication failed. Check credentials and try again.")
        finally:
            if c:
                c.close()

    inbox = db.query(ConferenceInbox).filter_by(id=inbox_id).first()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    _check_ownership(inbox, current_user)
    if inbox.type != "imap":
        raise HTTPException(400, "Only IMAP inboxes can be tested here.")
    if not inbox.imap_host:
        raise HTTPException(400, "IMAP host is not configured for this inbox.")
    try:
        import imaplib
        if inbox.imap_use_ssl:
            imap = imaplib.IMAP4_SSL(inbox.imap_host, inbox.imap_port or 993)
        else:
            imap = imaplib.IMAP4(inbox.imap_host, inbox.imap_port or 143)
        imap.login(inbox.email, _get_imap_password(inbox))
        imap.logout()
        return {"ok": True, "message": f"Connected to {inbox.imap_host} as {inbox.email}"}
    except Exception as e:
        logger.error("IMAP connection failed for inbox %s: %s", inbox_id, e, exc_info=True)
        raise HTTPException(400, "IMAP connection failed. Check host, port, and credentials.")


@router.delete("/inboxes/{inbox_id}")
def delete_inbox(inbox_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    inbox = db.query(ConferenceInbox).filter_by(id=inbox_id).first()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    _check_ownership(inbox, current_user)
    db.delete(inbox)
    db.commit()
    return {"ok": True}
