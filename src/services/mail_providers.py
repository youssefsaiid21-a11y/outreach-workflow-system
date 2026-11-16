"""
Mail provider abstraction for the unified /api/mailbox view.

A ``MailProvider`` normalizes Microsoft Graph into a single ``MailMessage``
shape that the frontend can render:

    {
        "id":          str,                 # provider-native message id
        "inbox_id":    str,                 # "outlook:<inbox_id>"
        "folder":      "inbox"|"sent",
        "thread_id":   str|None,
        "from_email":  str,
        "from_name":   str|None,
        "to":          [{"email": str, "name": str}],
        "subject":     str,
        "body_preview":str,
        "received_at": str|None,            # ISO 8601
        "sent_at":     str|None,
        "unread":      bool,
        "has_attachments": bool,
        "campaign_id": str|None,            # filled in by mailbox router
        "campaign_name": str|None,          # filled in by mailbox router
        "prospect_id": str|None,            # filled in by mailbox router
    }

And for ``get_message`` the same shape plus ``body_html`` / ``body_text``.

Each provider is constructed per-request by ``src/api/mailbox.py`` so the
HTTP clients they wrap are cleanly closed after the request.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from .outlook import OutlookClient

logger = logging.getLogger(__name__)


# ── Provider interface ───────────────────────────────────────────────────────


class MailProvider:
    """Duck-typed base class. Not enforced via Protocol to keep the typing
    cheap for the two concrete implementations below."""

    kind: str = ""

    def list_inboxes(self, *, user, db: Session) -> list[dict]:
        raise NotImplementedError

    def list_messages(
        self,
        *,
        inbox_key: str,
        folder: str,
        q: Optional[str],
        unread: Optional[bool],
        cursor: Optional[str],
        limit: int,
    ) -> dict:
        raise NotImplementedError

    def get_message(self, *, inbox_key: str, folder: str, msg_id: str) -> dict:
        raise NotImplementedError

    def send_reply(
        self,
        *,
        inbox_key: str,
        msg_id: str,
        body: str,
        subject: Optional[str] = None,
    ) -> dict:
        raise NotImplementedError

    def mark_read(self, *, inbox_key: str, msg_id: str, read: bool = True) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


# ── Outlook provider ─────────────────────────────────────────────────────────


class OutlookMailProvider(MailProvider):
    """Reads from and writes to a single Microsoft Graph mailbox.

    One provider instance corresponds to one user's Outlook mailbox. The
    ``user_inbox_id`` is the same string returned by
    ``src/api/outlook_auth.py::_user_outlook_id`` (e.g. ``"outlook_<user.id>"``).
    """

    kind = "outlook"

    def __init__(self, *, current_user, token_file):
        self.current_user = current_user
        self._user_inbox_id = self._derive_inbox_id(current_user)
        # Look up OutlookClient via module globals so tests can monkeypatch
        # ``mail_providers.OutlookClient`` without having to stub out the
        # real MSAL/Graph state.
        self.client = OutlookClient(token_file=token_file)

    @staticmethod
    def _derive_inbox_id(current_user) -> str:
        if current_user and getattr(current_user, "id", None):
            return f"outlook_{current_user.id}"
        return "outlook"

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass

    def _inbox_id_str(self) -> str:
        return f"outlook:{self._user_inbox_id}"

    def _verify_inbox_key(self, inbox_key: str) -> None:
        """Reject attempts to read another user's Outlook mailbox by guessing
        the inbox_id prefix. Raises a RuntimeError which the router maps to
        a 403."""
        if (inbox_key or "") != self._user_inbox_id:
            raise PermissionError(
                f"Outlook inbox {inbox_key!r} does not belong to current user"
            )

    # ── public API ───────────────────────────────────────────────────────

    def list_inboxes(self, *, user, db: Session) -> list[dict]:
        """Return a single-item list representing this user's mailbox, or
        an empty list if the user has never connected Outlook.

        We deliberately do NOT raise on a missing/expired token  -  the
        mailbox landing page should render cleanly even if Outlook is
        disconnected. A disconnected inbox is simply omitted.
        """
        try:
            email = self.client.get_mailbox_address()
        except Exception as exc:  # noqa: BLE001
            logger.warning("OutlookMailProvider.list_inboxes: get_mailbox_address failed: %s", exc)
            return []
        if not email:
            return []

        unread = 0
        try:
            page = self.client.list_folder_messages("inbox", unread=True, top=1)
            unread = len(page.get("items") or [])
            # Graph doesn't return a total count in this shape; the "1+"
            # heuristic below is enough to show a dot on the sidebar.
            if page.get("next_cursor"):
                unread = max(unread, 1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("OutlookMailProvider unread probe failed: %s", exc)

        return [
            {
                "id": self._inbox_id_str(),
                "kind": "outlook",
                "email": email,
                "label": f"{email} (Outlook)",
                "status": "active",
                "unread_count": unread,
                "capabilities": {
                    "sent": True,
                    "reply": True,
                    "mark_read": True,
                    "search": True,
                },
            }
        ]

    def list_messages(
        self,
        *,
        inbox_key: str,
        folder: str,
        q: Optional[str],
        unread: Optional[bool],
        cursor: Optional[str],
        limit: int,
    ) -> dict:
        self._verify_inbox_key(inbox_key)
        page = self.client.list_folder_messages(
            folder, q=q, unread=unread, cursor=cursor, top=limit
        )
        inbox_id = self._inbox_id_str()
        items = []
        for m in page.get("items") or []:
            m["inbox_id"] = inbox_id
            m["provider"] = "outlook"
            m["campaign_id"] = None
            m["campaign_name"] = None
            m["prospect_id"] = None
            items.append(m)
        return {"items": items, "next_cursor": page.get("next_cursor")}

    def get_message(self, *, inbox_key: str, folder: str, msg_id: str) -> dict:
        self._verify_inbox_key(inbox_key)
        m = self.client.get_message_full(msg_id)
        m["inbox_id"] = self._inbox_id_str()
        m["provider"] = "outlook"
        m.setdefault("campaign_id", None)
        m.setdefault("campaign_name", None)
        m.setdefault("prospect_id", None)
        return m

    def send_reply(
        self,
        *,
        inbox_key: str,
        msg_id: str,
        body: str,
        subject: Optional[str] = None,
    ) -> dict:
        self._verify_inbox_key(inbox_key)
        result = self.client.reply_to_message(msg_id, body)
        return {
            "sent": True,
            "provider_message_id": result.get("draft_id"),
            "reply_to": None,  # Graph handles reply-to automatically
            "subject": subject,
        }

    def mark_read(self, *, inbox_key: str, msg_id: str, read: bool = True) -> None:
        self._verify_inbox_key(inbox_key)
        self.client.mark_message_read(msg_id, read=read)
