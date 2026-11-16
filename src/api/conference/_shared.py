"""
Shared helpers, request models, and process-wide singletons for the
conference sub-package.

P4-1 / audit #6 MANUAL-1+5: this used to be the top of the 1805-LOC
``conference.py`` god-module. The decomposition split routes into
``routes_*.py``, business logic into ``service.py``, AI prompts into
``prompts.py``, and import parsing into ``parser.py``. Everything they
share  -  process-wide locks, the Anthropic client, the ownership guard,
the request models  -  lives here.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

import anthropic
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db.models import ConferenceLead, User

logger = logging.getLogger(__name__)


# ── Module-level singletons ──────────────────────────────────────────────────

# Shared Anthropic client with TTL refresh  -  avoids stale httpx pools in
# long-running workers while still reusing the connection across requests.
_anthropic_client = None
_anthropic_client_created_at = None
_ANTHROPIC_CLIENT_TTL = 3600  # 1 hour


def get_anthropic_client() -> anthropic.Anthropic:
    """Return (and lazily create / refresh) the process-wide Anthropic client."""
    global _anthropic_client, _anthropic_client_created_at
    now = time.time()
    if _anthropic_client is None or (now - _anthropic_client_created_at) > _ANTHROPIC_CLIENT_TTL:
        _anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        _anthropic_client_created_at = now
    return _anthropic_client


# Per-(lead_id, op) in-flight locks. FIX-19: stops the same lead being
# generated twice in parallel from concurrent requests on the same worker.
_INFLIGHT_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_INFLIGHT_LOCKS_GUARD = threading.Lock()


def _inflight_lock(lead_id: str, op: str) -> threading.Lock:
    """Get-or-create a per-(lead, operation) ``threading.Lock``."""
    key = (lead_id, op)
    with _INFLIGHT_LOCKS_GUARD:
        lock = _INFLIGHT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _INFLIGHT_LOCKS[key] = lock
        return lock


def _release_inflight_lock(lead_id: str, op: str, lock: threading.Lock):
    """Release and evict the in-flight lock to prevent unbounded dict growth."""
    lock.release()
    with _INFLIGHT_LOCKS_GUARD:
        _INFLIGHT_LOCKS.pop((lead_id, op), None)


# ── Upload & step constraints ────────────────────────────────────────────────

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB cap on conference imports

def _is_valid_step_name(name: str) -> bool:
    """Dynamic step-name validation that supports any emailN index."""
    if name in ("linkedin_connect_note", "linkedin_message"):
        return True
    if name.startswith("email"):
        try:
            int(name.replace("email", ""))
            return True
        except ValueError:
            return False
    return False


# ── Request / Response models ────────────────────────────────────────────────


class GenerateRequest(BaseModel):
    lead_id: str
    context: str


class BulkGenerateRequest(BaseModel):
    lead_ids: list[str]
    context: str
    include_booking_link: bool = True


class SaveEmailRequest(BaseModel):
    subject: str
    body: str


class SaveSequenceEmailRequest(BaseModel):
    step: str          # e.g. "email1", "email2"
    subject: str
    body: str


class InboxCreate(BaseModel):
    name: str
    email: str
    type: str                          # "outlook" | "imap"
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_password: Optional[str] = None
    imap_use_ssl: bool = True


class BulkDeleteRequest(BaseModel):
    """FIX-25: bulk delete payload  -  list of conference lead ids."""
    ids: list[str]


# ── Ownership guard ──────────────────────────────────────────────────────────


def _check_ownership(obj, current_user: "User | None", db: "Session | None" = None):
    """
    FIX-13: Fail-closed ownership check.

    Refuses if ``current_user`` is None and any users exist in the DB
    (auth was skipped or misrouted). Refuses if the object has no
    ``user_id`` (legacy unscoped row  -  must be backfilled to admin
    first). Refuses on mismatch.
    """
    obj_user_id = getattr(obj, "user_id", None)
    if current_user is None:
        # If users exist in the system, anonymous access is never allowed.
        if db is not None:
            try:
                if db.query(User).count() > 0:
                    raise HTTPException(401, "Authentication required.")
            except HTTPException:
                raise
            except Exception:
                pass
        # If no users exist (first-run dev mode), allow only when row is unowned.
        if obj_user_id is None:
            return
        raise HTTPException(403, "Access denied.")
    if obj_user_id is None:
        # Backfill should have caught this  -  refuse rather than silently leak.
        raise HTTPException(403, "Access denied.")
    if obj_user_id != current_user.id:
        raise HTTPException(403, "Access denied.")


# ── Outlook token cache resolution (P3-3) ────────────────────────────────────


def _outlook_token_file(current_user: "User | None") -> Path:
    """
    Per-user Outlook token cache file path.

    **Split-brain fix (2026-04-09):** the earlier P3-3 refactor moved this
    reader to look inside a ``tokens/`` subdirectory with filenames like
    ``outlook_{user_id}.json``, but the canonical writer  -  the OAuth
    device-code flow in ``src/api/outlook_auth.py``  -  was never updated
    and still writes to ``outreach-system/token_cache_outlook_{user_id}.json``
    (no subdirectory, ``token_cache_`` prefix). The ``tokens/`` directory
    never existed on prod, so every conference draft after P3-3 failed
    with "Outlook is not connected for this user" even for users who
    had just clicked "Connect Outlook" successfully seconds earlier.

    This function now points at the same path that ``outlook_auth._cache_file``
    uses for ``inbox_id = f"outlook_{user_id}"``, so the reader and writer
    agree again. Any legacy tokens in ``tokens/outlook_{user_id}.json`` are
    still honoured as a fallback so users who had connected via the style
    sync flow (which uses that path) keep working without re-auth.
    """
    base = Path(__file__).resolve().parent.parent.parent.parent  # outreach-system/
    if current_user:
        # Canonical path  -  written by outlook_auth.py OAuth flow.
        canonical = base / f"token_cache_outlook_{current_user.id}.json"
        if canonical.exists():
            return canonical
        # Legacy fallback  -  written by the old style.py sync path.
        legacy = base / "tokens" / f"outlook_{current_user.id}.json"
        if legacy.exists():
            return legacy
        # Neither exists  -  return the canonical path so the "not connected"
        # error points at the right place and a fresh OAuth flow lands it
        # where the readers expect.
        return canonical
    return base / "token_cache.json"


# ── String / JSON helpers ────────────────────────────────────────────────────

# Greeting patterns: matches "Hi John,", "Hello there,", "Dear Mr. Smith,",
# "Good morning,", "Hey Sam," etc. Case-insensitive, allows trailing comma or colon.
_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|dear|greetings|good\s+(morning|afternoon|evening))\b[^\n]{0,60}[,:]?\s*$",
    re.IGNORECASE,
)


def _strip_greeting(body: str) -> str:
    """Remove the first line if it looks like a greeting. Used when an
    Outlook signature already provides one and we want to avoid
    duplication."""
    if not body:
        return body
    lines = body.split("\n", 1)
    if not lines:
        return body
    first = lines[0].strip()
    if _GREETING_RE.match(first):
        rest = lines[1] if len(lines) > 1 else ""
        # Also drop the blank line after the greeting if present
        return rest.lstrip("\n")
    return body


# 2026-04-09: signoff stripping helper moved to ``src/services/signoff.py`` so
# the conference email generation path can reuse it. We re-export
# under the legacy private names so existing imports in this package keep
# working without churn.
from ...services.signoff import strip_signoff as _strip_signoff  # noqa: E402,F401
from ...services.signoff import SIGNOFF_LINE_RE as _SIGNOFF_LINE_RE  # noqa: E402,F401


def _safe_json_loads(raw: str | None):
    """Parse JSON string, returning None instead of crashing on malformed data."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Malformed JSON in database field, returning None. Preview: %s", raw[:120])
        return None


def _lead_to_dict(lead: ConferenceLead) -> dict:
    return {
        "id": lead.id,
        "conference_name": lead.conference_name,
        "outreach_type": getattr(lead, "outreach_type", "post"),
        "event_date": lead.event_date.isoformat() if getattr(lead, "event_date", None) else None,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "email": lead.email,
        "company": lead.company,
        "title": lead.title,
        "notes": lead.notes,
        "linkedin_url": lead.linkedin_url,
        "company_website": lead.company_website,
        "cc": lead.cc,
        "category": lead.category,
        "linkedin_headline": lead.linkedin_headline,
        "company_description": lead.company_description,
        "raw_data": _safe_json_loads(lead.raw_data),
        "enriched_at": lead.enriched_at.isoformat() if lead.enriched_at else None,
        "generated_subject": lead.generated_subject,
        "generated_body": lead.generated_body,
        "generated_at": lead.generated_at.isoformat() if lead.generated_at else None,
        "generated_sequence": _safe_json_loads(lead.generated_sequence),
        "sequence_generated_at": lead.sequence_generated_at.isoformat() if lead.sequence_generated_at else None,
        "draft_message_id": lead.draft_message_id,
        "draft_created_at": lead.draft_created_at.isoformat() if lead.draft_created_at else None,
        # Events-reply-cutoff 2026-04-09: reply tracking fields. The
        # dashboard uses ``replied_at`` as a boolean gate ("is this lead
        # off-limits for further drafting?") and shows the subject/snippet
        # on hover so the rep can see what the prospect actually said.
        "replied_at": lead.replied_at.isoformat() if lead.replied_at else None,
        "reply_subject": lead.reply_subject,
        "reply_snippet": lead.reply_snippet,
        "reply_from_email": lead.reply_from_email,
        "meeting_booked": lead.meeting_booked,
        "user_note": lead.user_note,
    }


def _assert_not_replied(lead: ConferenceLead) -> None:
    """
    Guard called at the top of every draft endpoint. Refuses further
    drafting for a lead that has already replied.

    Raises HTTPException(409) with an operator-actionable message. The
    UI catches the 409 and shows a toast so the rep knows exactly why
    the button did nothing.

    Why 409 Conflict rather than 400: the request itself is well-formed;
    the action is refused because the lead's state (replied) makes it
    invalid. 409 is the semantic fit and the existing hardening flows
    (suppression pre-flight, launch lock) already use 409 for the same
    shape of "refusing because of state".
    """
    if lead.replied_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Lead {lead.email} has replied to your outreach  -  no further "
                f"drafts will be created for this lead. To override (e.g. the "
                f"reply was an out-of-office), POST "
                f"/api/conference/leads/{lead.id}/clear-reply first."
            ),
        )
