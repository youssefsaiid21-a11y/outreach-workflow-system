"""
Unified mailbox API  -  /api/mailbox/*

Provides a mail-client view for Outlook mailboxes linked to each user.
All message normalization lives in ``src/services/mail_providers.py``.
Campaign annotation matches messages to DB campaigns via Prospect.email.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import Campaign, EmailSequence, Prospect, Reply, User
from ..services.mail_providers import OutlookMailProvider
from .auth import get_current_user
from .conference._shared import _outlook_token_file
from .outlook_auth import _user_outlook_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mailbox", tags=["mailbox"])


def _user_campaign_ids(db, user: Optional[User]) -> Optional[list[str]]:
    """Return campaign IDs visible to the user, or None for admins (all campaigns)."""
    if user is None:
        return None
    from ..db.models import UserRole
    if hasattr(user, "role") and user.role == UserRole.admin:
        return None
    rows = db.query(Campaign.id).filter(Campaign.user_id == user.id).all()
    return [r[0] for r in rows]


# ── Request / response models ────────────────────────────────────────────────


class ReplyRequest(BaseModel):
    body: str
    subject: Optional[str] = None


# ── Provider dispatch ────────────────────────────────────────────────────────


def _resolve_provider(inbox_id: str, current_user: Optional[User]):
    """Parse the opaque inbox id and return ``(provider, inbox_key)``.

    Inbox ids are flat strings of the form ``<kind>:<key>`` so the
    frontend can echo them back without having to persist any structured
    state. The dispatcher enforces that an ``outlook:`` id matches the
    current user's outlook mailbox  -  otherwise one user could read
    another user's mail by guessing an id.
    """
    if not inbox_id or ":" not in inbox_id:
        raise HTTPException(400, "inbox must be of the form '<kind>:<key>'")
    kind, _, key = inbox_id.partition(":")
    if kind == "outlook":
        expected = _user_outlook_id(current_user)
        if key != expected:
            raise HTTPException(403, "Outlook inbox does not belong to current user")
        token_file = _outlook_token_file(current_user)
        if not token_file.exists():
            raise HTTPException(
                404,
                "Outlook is not connected for this user. "
                "Connect via the Senders tab first.",
            )
        return OutlookMailProvider(current_user=current_user, token_file=token_file), key
    raise HTTPException(400, f"Unknown inbox kind: {kind!r}")


# ── Campaign annotation ──────────────────────────────────────────────────────


def _annotate_campaigns(
    db: Session,
    messages: list[dict],
    user_campaign_ids: Optional[list[str]],
) -> list[dict]:
    """Attach ``campaign_id``, ``campaign_name`` and ``prospect_id`` to each
    message where we can resolve them.

    Outlook messages carry no campaign linkage from the provider. We match them
    by sender (for inbox folder) or first recipient (for sent folder) against
    ``Prospect.email``, then join to ``EmailSequence`` to pick up a campaign
    that the current user owns.
    """
    if not messages:
        return messages
    scope = set(user_campaign_ids) if user_campaign_ids is not None else None

    # Pass 1  -  collect sender/recipient emails we need to look up
    emails_to_lookup: set[str] = set()
    for m in messages:
        if m.get("folder") == "sent":
            to_list = m.get("to") or []
            if to_list and to_list[0].get("email"):
                m["_match_email"] = to_list[0]["email"].lower()
                emails_to_lookup.add(m["_match_email"])
        else:
            sender = (m.get("from_email") or "").lower()
            if sender:
                m["_match_email"] = sender
                emails_to_lookup.add(sender)

    # Pass 2  -  batch email → (campaign_id, prospect_id) lookup
    seq_by_email: dict[str, tuple[str, str]] = {}
    if emails_to_lookup:
        rows = (
            db.query(Prospect.email, EmailSequence.campaign_id, EmailSequence.prospect_id)
            .join(EmailSequence, EmailSequence.prospect_id == Prospect.id)
            .filter(Prospect.email.in_(list(emails_to_lookup)))
            .all()
        )
        for p_email, camp_id, prospect_id in rows:
            if not p_email:
                continue
            le = p_email.lower()
            if scope is not None and camp_id not in scope:
                continue
            seq_by_email.setdefault(le, (camp_id, prospect_id))

    # Pass 3  -  apply matches and collect campaign ids for name lookup
    camp_ids_to_name: set[str] = set()
    for m in messages:
        me = m.get("_match_email")
        if me and me in seq_by_email:
            cid, pid = seq_by_email[me]
            m["campaign_id"] = cid
            m["prospect_id"] = pid
            camp_ids_to_name.add(cid)

    if camp_ids_to_name:
        name_by_id = {
            c.id: c.name
            for c in db.query(Campaign.id, Campaign.name)
            .filter(Campaign.id.in_(list(camp_ids_to_name)))
            .all()
        }
    else:
        name_by_id = {}

    for m in messages:
        m.setdefault("campaign_id", None)
        m.setdefault("prospect_id", None)
        if m.get("campaign_id"):
            m["campaign_name"] = name_by_id.get(m["campaign_id"])
        else:
            m["campaign_name"] = None
        m.pop("_match_email", None)

    return messages


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/inboxes")
def list_inboxes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return every inbox the current user can act on.

    Only Outlook mailboxes are listed. A disconnected Outlook mailbox is
    silently omitted  -  the Senders tab is where operators go to (re)connect.
    """
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")

    items: list[dict] = []

    try:
        token_file = _outlook_token_file(current_user)
        if token_file.exists():
            outlook_provider = OutlookMailProvider(
                current_user=current_user, token_file=token_file
            )
            try:
                items.extend(outlook_provider.list_inboxes(user=current_user, db=db))
            finally:
                outlook_provider.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("mailbox.list_inboxes: outlook list failed: %s", exc)

    return {"items": items, "total": len(items)}


@router.get("/messages")
def list_messages(
    inbox: str = Query(..., description="Inbox id in the form '<kind>:<key>'"),
    folder: str = Query("inbox", pattern="^(inbox|sent)$"),
    q: Optional[str] = Query(None, description="Full-text search"),
    campaign_id: Optional[str] = Query(None),
    unread: Optional[bool] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch one page of messages for a single inbox/folder and annotate
    each row with the owning campaign (if any)."""
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")

    user_campaign_ids = _user_campaign_ids(db, current_user)
    if campaign_id and user_campaign_ids is not None and campaign_id not in user_campaign_ids:
        raise HTTPException(404, "Campaign not found")

    provider, inbox_key = _resolve_provider(inbox, current_user)
    try:
        page = provider.list_messages(
            inbox_key=inbox_key,
            folder=folder,
            q=q,
            unread=unread,
            cursor=cursor,
            limit=limit,
        )
    except PermissionError as exc:
        logger.warning("mailbox.list_messages: permission denied: %s", exc)
        raise HTTPException(403, "Access denied.") from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "mailbox.list_messages: provider %s failed: %s",
            getattr(provider, "kind", "?"), exc,
        )
        raise HTTPException(502, "Failed to fetch messages from provider.") from exc
    finally:
        provider.close()

    items = page.get("items") or []
    items = _annotate_campaigns(db, items, user_campaign_ids)

    if campaign_id:
        items = [m for m in items if m.get("campaign_id") == campaign_id]

    return {
        "items": items,
        "next_cursor": page.get("next_cursor"),
        "has_more": bool(page.get("next_cursor")),
        "inbox": inbox,
        "folder": folder,
    }


@router.get("/messages/{msg_id}")
def get_message(
    msg_id: str,
    inbox: str = Query(...),
    folder: str = Query("inbox", pattern="^(inbox|sent)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a single message with its full body and recipient list."""
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")

    user_campaign_ids = _user_campaign_ids(db, current_user)

    provider, inbox_key = _resolve_provider(inbox, current_user)
    try:
        msg = provider.get_message(inbox_key=inbox_key, folder=folder, msg_id=msg_id)
    except PermissionError as exc:
        logger.warning("mailbox.get_message: permission denied: %s", exc)
        raise HTTPException(403, "Access denied.") from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("mailbox.get_message: %s", exc)
        raise HTTPException(502, "Failed to fetch message from provider.") from exc
    finally:
        provider.close()

    _annotate_campaigns(db, [msg], user_campaign_ids)
    return msg


@router.post("/messages/{msg_id}/reply")
def reply_to_message(
    msg_id: str,
    req: ReplyRequest,
    inbox: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Actually send a reply. Unlike the legacy
    ``/inbox-mgmt/emails/{id}/reply`` endpoint, this one puts real mail on
    the wire  -  Legacy — Instantly removed; replies handled via Outlook Graph
    three-step createReply flow.

    On success we also write a lightweight ``Reply`` row with
    ``classification="outbound_reply"`` so the legacy Inbox view still
    reflects that outbound activity happened. Failure to write the audit
    row never blocks the send  -  the provider call is the source of
    truth.
    """
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")
    if not (req.body or "").strip():
        raise HTTPException(422, "Reply body is required.")

    provider, inbox_key = _resolve_provider(inbox, current_user)
    try:
        result = provider.send_reply(
            inbox_key=inbox_key,
            msg_id=msg_id,
            body=req.body,
            subject=req.subject,
        )
    except PermissionError as exc:
        logger.warning("mailbox.reply: permission denied: %s", exc)
        raise HTTPException(403, "Access denied.") from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("mailbox.reply: send_reply failed: %s", exc)
        raise HTTPException(502, "Reply send failed. Please try again.") from exc
    finally:
        provider.close()

    # Best-effort audit row so the legacy Inbox tab can show that the
    # user replied. Only attempt when we can resolve a prospect from
    # the original message context.
    reply_row_id: Optional[str] = None
    try:
        # Re-open a provider to fetch the original message for context  -
        # expensive-ish but only on send, not on read.
        ctx_provider, _k = _resolve_provider(inbox, current_user)
        try:
            original = ctx_provider.get_message(
                inbox_key=inbox_key, folder="inbox", msg_id=msg_id
            )
        finally:
            ctx_provider.close()
        user_campaign_ids = _user_campaign_ids(db, current_user)
        _annotate_campaigns(db, [original], user_campaign_ids)
        prospect_id = original.get("prospect_id")
        campaign_id = original.get("campaign_id")
        if prospect_id:
            now = datetime.now(timezone.utc)
            row = Reply(
                id=str(uuid.uuid4()),
                prospect_id=prospect_id,
                campaign_id=campaign_id,
                subject=f"Re: {original.get('subject') or ''}",
                body=req.body,
                from_email=(result.get("reply_to") or original.get("from_email") or ""),
                classification="outbound_reply",
                received_at=now,
                classified_at=now,
            )
            db.add(row)
            db.commit()
            reply_row_id = row.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("mailbox.reply: audit Reply row write failed: %s", exc)
        db.rollback()

    return {
        "sent": True,
        "provider_message_id": result.get("provider_message_id"),
        "subject": result.get("subject"),
        "reply_logged_id": reply_row_id,
    }


@router.post("/messages/{msg_id}/mark-read")
def mark_read(
    msg_id: str,
    inbox: str = Query(...),
    read: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user is None and db.query(User).count() > 0:
        raise HTTPException(401, "Authentication required.")
    provider, inbox_key = _resolve_provider(inbox, current_user)
    try:
        provider.mark_read(inbox_key=inbox_key, msg_id=msg_id, read=read)
    except PermissionError as exc:
        logger.warning("mailbox.mark_read: permission denied: %s", exc)
        raise HTTPException(403, "Access denied.") from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("mailbox.mark_read: %s", exc)
        return {"ok": False, "error": "Failed to update read status."}
    finally:
        provider.close()
    return {"ok": True}
