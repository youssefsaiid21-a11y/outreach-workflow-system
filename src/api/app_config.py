"""
Dashboard config endpoint (2026-04-11 hardcoded-values audit).

Exposes the small set of sequence-shape constants the frontend needs
to render monitor views correctly without baking magic numbers into
the JavaScript bundle. Historically the frontend mirrored
``_DEFAULT_EMAIL_DAYS = [1, 7, 12, 18, 24, 30]`` and the LinkedIn day
offsets in ``main.js``  -  any change to the backend constants required
a coordinated code-and-frontend deploy or the two would silently drift.

This endpoint is the single source of truth. The frontend calls it
once at bootApp() time and caches the result; every downstream renderer
uses the fetched values instead of its own hardcoded mirror.

Endpoint: ``GET /config/sequence-defaults``

Response shape::

    {
        "max_sequence_emails": 6,
        "default_email_day_offsets": [1, 7, 12, 18, 24, 30],
        "default_linkedin_connect_day": 4,
        "default_linkedin_message_day": 16,
        "default_email_word_limit": 50,
        "email_word_limits": {"email1": 100, "email2": 60, ...},
        "linkedin_word_limits": {"linkedin_connect_note": 30, ...}
    }
"""
from __future__ import annotations

from fastapi import APIRouter

from ..config import (
    DEFAULT_EMAIL_DAY_OFFSETS,
    DEFAULT_EMAIL_WORD_LIMIT,
    DEFAULT_LINKEDIN_CONNECT_DAY,
    DEFAULT_LINKEDIN_MESSAGE_DAY,
    EMAIL_WORD_LIMITS,
    LINKEDIN_WORD_LIMITS,
    MAX_SEQUENCE_EMAILS,
)

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/sequence-defaults")
def get_sequence_defaults() -> dict:
    """Return the sequence-shape constants the dashboard needs to render
    monitor/builder views. Authenticated read-only  -  contains no
    campaign data, no secrets, just the same literal values that ship
    in the Python source."""
    return {
        "max_sequence_emails": MAX_SEQUENCE_EMAILS,
        "default_email_day_offsets": list(DEFAULT_EMAIL_DAY_OFFSETS),
        "default_linkedin_connect_day": DEFAULT_LINKEDIN_CONNECT_DAY,
        "default_linkedin_message_day": DEFAULT_LINKEDIN_MESSAGE_DAY,
        "default_email_word_limit": DEFAULT_EMAIL_WORD_LIMIT,
        "email_word_limits": dict(EMAIL_WORD_LIMITS),
        "linkedin_word_limits": dict(LINKEDIN_WORD_LIMITS),
    }
