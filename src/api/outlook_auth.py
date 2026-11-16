"""
Outlook / Microsoft Graph OAuth via Device Code Flow.

Design principles:
- All state lives in files (token_cache_*.json, flow_*.json) — survives server reloads.
- /initiate  → writes flow file, returns user_code immediately.
- /complete  → starts a background thread, returns immediately (browser polls /status).
- Background thread does NOT delete the flow file until auth succeeds or definitively fails.
  This means a server reload mid-auth is safe: resume_pending_flows() restarts the thread.
- /status    → loads MSAL SerializableTokenCache from disk, checks for valid token.
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import User
from .auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/outlook", tags=["outlook"])

TOKEN_DIR = Path(__file__).parent.parent.parent   # outreach-system/
# Events-reminders 2026-04-09: mirror the scope split from
# src/services/outlook.py. The dashboard "Connect Outlook" button hits
# THIS module's device-code flow, so the Calendars.ReadWrite scope must
# be requested here too — otherwise clicking Reconnect only re-grants
# Mail.* and the calendar reminder feature silently stays disabled.
#
# MAIL_SCOPES is the minimum needed to send drafts. CALENDAR_SCOPES is
# optional; if a user dismisses the calendar consent screen the mail
# token still works and the calendar path degrades gracefully via
# OutlookClient._get_calendar_token / has_calendar_scope.
MAIL_SCOPES = ["Mail.Read", "Mail.ReadWrite", "Mail.Send"]
CALENDAR_SCOPES = ["Calendars.ReadWrite"]
SCOPES = MAIL_SCOPES + CALENDAR_SCOPES


# ── Validation ────────────────────────────────────────────────────────────────

_VALID_INBOX_ID = re.compile(r'^[a-zA-Z0-9_-]{1,100}$')

def _validate_inbox_id(inbox_id: str):
    if not _VALID_INBOX_ID.match(inbox_id):
        raise HTTPException(400, "Invalid inbox_id")


# ── File paths ────────────────────────────────────────────────────────────────

def _cache_file(inbox_id: str) -> Path:
    if inbox_id == "outlook":
        name = "token_cache.json"
    else:
        name = f"token_cache_{inbox_id}.json"
    return TOKEN_DIR / name

def _flow_file(inbox_id: str) -> Path:
    return TOKEN_DIR / f"flow_{inbox_id}.json"


# ── MSAL helpers ──────────────────────────────────────────────────────────────

def _make_app(inbox_id: str):
    """Create a PublicClientApplication backed by a persisted token cache.

    Device code flow requires PublicClientApplication. The Azure app must
    have 'Allow public client flows' set to Yes in Authentication settings.
    """
    import msal
    cache = msal.SerializableTokenCache()
    cf = _cache_file(inbox_id)
    if cf.exists():
        cache.deserialize(cf.read_text(encoding="utf-8"))
    app = msal.PublicClientApplication(
        client_id=os.getenv("MICROSOFT_CLIENT_ID"),
        authority=f"https://login.microsoftonline.com/{os.getenv('MICROSOFT_TENANT_ID', 'common')}",
        token_cache=cache,
    )
    return app, cache

def _persist_cache(inbox_id: str, cache):
    if cache.has_state_changed:
        cf = _cache_file(inbox_id)
        cf.write_text(cache.serialize(), encoding="utf-8")
        try:
            os.chmod(str(cf), 0o600)
        except OSError:
            pass


# ── Auth check ────────────────────────────────────────────────────────────────

def _check_auth(inbox_id: str) -> Optional[dict]:
    """Return {authenticated, account} if a valid token exists, else None."""
    if not _cache_file(inbox_id).exists():
        return None
    app, cache = _make_app(inbox_id)
    accounts = app.get_accounts()
    if not accounts:
        return None
    # Silent refresh with MAIL_SCOPES only — see SCOPES comment above.
    # A cached token that covers only Mail.* is still "authenticated" for
    # the purposes of the dashboard status badge; calendar functionality
    # is gated separately via OutlookClient.has_calendar_scope().
    result = app.acquire_token_silent(MAIL_SCOPES, account=accounts[0])
    _persist_cache(inbox_id, cache)
    if result and "access_token" in result:
        return {"authenticated": True, "account": accounts[0].get("username")}
    return None


# ── Background auth thread ────────────────────────────────────────────────────

def _auth_worker(inbox_id: str):
    """
    Poll Microsoft until the user signs in or the code expires.
    The flow file is left in place until this function exits so that
    a server reload can resume via resume_pending_flows().
    Only deletes the flow file if it still belongs to THIS flow (race-condition safe).
    """
    ff = _flow_file(inbox_id)
    if not ff.exists():
        return

    flow = json.loads(ff.read_text(encoding="utf-8"))
    my_code = flow.get("user_code")  # used to detect if a newer flow replaced ours

    # Bail early if flow already expired
    if time.time() > flow.get("expires_at", float("inf")):
        _delete_flow_if_mine(inbox_id, my_code)
        logger.warning("Auth flow already expired for inbox %s", inbox_id)
        return

    logger.info("Auth polling started for inbox %s (%s)", inbox_id, my_code)
    app, cache = _make_app(inbox_id)

    try:
        result = app.acquire_token_by_device_flow(flow)
    except Exception as exc:
        # Interrupted (server reload, etc.) — leave flow file so we can resume
        logger.warning("Auth polling interrupted for inbox %s: %s", inbox_id, exc)
        return

    if "access_token" in result:
        # Always save — don't rely solely on has_state_changed
        _cache_file(inbox_id).write_text(cache.serialize(), encoding="utf-8")
        accounts = app.get_accounts()
        account = accounts[0].get("username") if accounts else "unknown"
        logger.info("Outlook auth complete for inbox %s (%s)", inbox_id, account)
        (TOKEN_DIR / f"auth_result_{inbox_id}.json").write_text(json.dumps({"status": "success", "account": account}), encoding="utf-8")
    else:
        err = result.get("error_description") or result.get("error", "unknown")
        logger.warning("Outlook auth failed for inbox %s: %s", inbox_id, err)
        (TOKEN_DIR / f"auth_result_{inbox_id}.json").write_text(json.dumps({"status": "failed", "error": result.get("error"), "error_description": result.get("error_description"), "full": result}), encoding="utf-8")

    # Only delete if the flow file still belongs to THIS auth attempt
    _delete_flow_if_mine(inbox_id, my_code)


def _delete_flow_if_mine(inbox_id: str, my_code: str):
    """Delete the flow file only if it still contains our user_code (not a newer flow)."""
    ff = _flow_file(inbox_id)
    try:
        if ff.exists():
            current_code = json.loads(ff.read_text(encoding="utf-8")).get("user_code")
            if current_code == my_code:
                ff.unlink(missing_ok=True)
    except Exception:
        pass


def _start_auth_thread(inbox_id: str):
    t = threading.Thread(
        target=_auth_worker, args=(inbox_id,),
        name=f"outlook-auth-{inbox_id}", daemon=True,
    )
    t.start()


# ── Resume interrupted flows on server startup ────────────────────────────────

def resume_pending_flows():
    for ff in TOKEN_DIR.glob("flow_*.json"):
        inbox_id = ff.stem[len("flow_"):]
        try:
            flow = json.loads(ff.read_text(encoding="utf-8"))
            if time.time() < flow.get("expires_at", 0):
                logger.info("Resuming auth flow for inbox %s", inbox_id)
                _start_auth_thread(inbox_id)
            else:
                ff.unlink(missing_ok=True)
                logger.info("Cleaned up expired flow for inbox %s", inbox_id)
        except Exception as exc:
            logger.warning("Could not resume flow %s: %s", ff, exc)


# Called when this module is first imported (i.e. on every server start/reload)
resume_pending_flows()


# ── API endpoints ─────────────────────────────────────────────────────────────

def _result_file(inbox_id: str) -> Path:
    return TOKEN_DIR / f"auth_result_{inbox_id}.json"

def _status(inbox_id: str) -> dict:
    info = _check_auth(inbox_id)
    if info:
        # Clean up stale result file on success
        _result_file(inbox_id).unlink(missing_ok=True)
        return info
    # Report if an auth is currently in progress
    if _flow_file(inbox_id).exists():
        return {"authenticated": False, "pending": True}
    # Report if the last auth attempt definitively failed
    rf = _result_file(inbox_id)
    if rf.exists():
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
            if data.get("status") == "failed":
                rf.unlink(missing_ok=True)
                return {"authenticated": False, "failed": True, "error": data.get("error_description") or data.get("error")}
        except Exception:
            rf.unlink(missing_ok=True)
    return {"authenticated": False}

def _initiate(inbox_id: str) -> dict:
    # Already connected?
    info = _check_auth(inbox_id)
    if info:
        return {"already_authenticated": True, "account": info["account"]}

    app, _ = _make_app(inbox_id)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise HTTPException(500, f"Failed to start device flow: {flow}")

    _flow_file(inbox_id).write_text(json.dumps(flow), encoding="utf-8")
    return {
        "already_authenticated": False,
        "user_code": flow["user_code"],
        "verification_uri": flow["verification_uri"],
        "message": flow["message"],
        "expires_in": flow.get("expires_in", 900),
    }

def _complete(inbox_id: str) -> dict:
    if not _flow_file(inbox_id).exists():
        raise HTTPException(400, "No active auth flow — call /initiate first.")
    _start_auth_thread(inbox_id)
    return {"ok": True, "polling": True}

def _disconnect(inbox_id: str) -> dict:
    for f in (_cache_file(inbox_id), _flow_file(inbox_id)):
        f.unlink(missing_ok=True)
    return {"ok": True}


def _user_outlook_id(current_user: "User | None") -> str:
    """Return a per-user inbox ID for the default Outlook connection."""
    if current_user and current_user.id:
        return f"outlook_{current_user.id}"
    return "outlook"


# Default (personal) Outlook endpoints — per-user token isolation
@router.get("/auth/status")
def auth_status(current_user: User = Depends(get_current_user)):
    return _status(_user_outlook_id(current_user))

@router.post("/auth/initiate")
def auth_initiate(current_user: User = Depends(get_current_user)):
    return _initiate(_user_outlook_id(current_user))

@router.post("/auth/complete")
def auth_complete(current_user: User = Depends(get_current_user)):
    return _complete(_user_outlook_id(current_user))

@router.post("/auth/disconnect")
def auth_disconnect(current_user: User = Depends(get_current_user)):
    return _disconnect(_user_outlook_id(current_user))


# Per-inbox endpoints
@router.get("/inbox/{inbox_id}/auth/status")
def inbox_auth_status(inbox_id: str):
    _validate_inbox_id(inbox_id)
    return _status(inbox_id)

@router.post("/inbox/{inbox_id}/auth/initiate")
def inbox_auth_initiate(inbox_id: str):
    _validate_inbox_id(inbox_id)
    return _initiate(inbox_id)

@router.post("/inbox/{inbox_id}/auth/complete")
def inbox_auth_complete(inbox_id: str):
    _validate_inbox_id(inbox_id)
    return _complete(inbox_id)

@router.post("/inbox/{inbox_id}/auth/disconnect")
def inbox_auth_disconnect(inbox_id: str):
    _validate_inbox_id(inbox_id)
    return _disconnect(inbox_id)
