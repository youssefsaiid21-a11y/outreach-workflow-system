"""CRUD API for LinkedIn accounts — first-class entities for multi-account LinkedIn automation.

Each LinkedInAccount represents a person's LinkedIn profile + their PhantomBuster
phantom configuration. Campaigns reference accounts via scrape_account_id (who
finds leads via Sales Nav) and outreach_account_id (who sends connects/messages).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import LinkedInAccount, User
from ..services.crypto import encrypt, decrypt, ENCRYPTION_AVAILABLE
from .auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/linkedin-accounts", tags=["linkedin-accounts"])


# ── Request / Response models ────────────────────────────────────────────────

class LinkedInAccountCreate(BaseModel):
    name: str
    linkedin_profile_url: Optional[str] = None
    pb_api_key: Optional[str] = None  # plaintext; encrypted before storage
    sales_nav_agent_id: Optional[str] = None
    connect_agent_id: Optional[str] = None
    message_agent_id: Optional[str] = None
    inbox_scraper_agent_id: Optional[str] = None


class LinkedInAccountUpdate(BaseModel):
    name: Optional[str] = None
    linkedin_profile_url: Optional[str] = None
    pb_api_key: Optional[str] = None  # plaintext; encrypted before storage
    sales_nav_agent_id: Optional[str] = None
    connect_agent_id: Optional[str] = None
    message_agent_id: Optional[str] = None
    inbox_scraper_agent_id: Optional[str] = None


def _account_to_dict(acct: LinkedInAccount) -> dict:
    """Serialize a LinkedInAccount to a JSON-safe dict, masking the PB API key."""
    pb_key_masked = None
    if acct.pb_api_key_encrypted:
        try:
            raw = decrypt(acct.pb_api_key_encrypted)
            pb_key_masked = "****" + raw[-4:] if len(raw) > 8 else "****"
        except Exception:
            pb_key_masked = "****decryption-error****"
    return {
        "id": acct.id,
        "user_id": acct.user_id,
        "name": acct.name,
        "linkedin_profile_url": acct.linkedin_profile_url,
        "pb_api_key_masked": pb_key_masked,
        "has_pb_api_key": acct.pb_api_key_encrypted is not None,
        "sales_nav_agent_id": acct.sales_nav_agent_id,
        "connect_agent_id": acct.connect_agent_id,
        "message_agent_id": acct.message_agent_id,
        "inbox_scraper_agent_id": acct.inbox_scraper_agent_id,
        "is_active": acct.is_active,
        "created_at": acct.created_at.isoformat() if acct.created_at else None,
        "updated_at": acct.updated_at.isoformat() if acct.updated_at else None,
    }


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
def list_linkedin_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all active LinkedIn accounts."""
    if not current_user:
        raise HTTPException(401, "Authentication required")
    accounts = (
        db.query(LinkedInAccount)
        .filter(LinkedInAccount.is_active == True)  # noqa: E712
        .order_by(LinkedInAccount.created_at.asc())
        .all()
    )
    return {"items": [_account_to_dict(a) for a in accounts]}


@router.post("")
def create_linkedin_account(
    body: LinkedInAccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new LinkedIn account."""
    if not current_user:
        raise HTTPException(401, "Authentication required")

    encrypted_key = None
    if body.pb_api_key:
        if not ENCRYPTION_AVAILABLE:
            raise HTTPException(500, "Encryption not available on server. Contact admin.")
        encrypted_key = encrypt(body.pb_api_key.strip())

    acct = LinkedInAccount(
        user_id=current_user.id,
        name=body.name.strip(),
        linkedin_profile_url=(body.linkedin_profile_url or "").strip() or None,
        pb_api_key_encrypted=encrypted_key,
        sales_nav_agent_id=(body.sales_nav_agent_id or "").strip() or None,
        connect_agent_id=(body.connect_agent_id or "").strip() or None,
        message_agent_id=(body.message_agent_id or "").strip() or None,
        inbox_scraper_agent_id=(body.inbox_scraper_agent_id or "").strip() or None,
        is_active=True,
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    logger.info("Created LinkedIn account '%s' (id=%s) by user=%s", acct.name, acct.id, current_user.id)
    return _account_to_dict(acct)


@router.patch("/{account_id}")
def update_linkedin_account(
    account_id: str,
    body: LinkedInAccountUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update an existing LinkedIn account."""
    if not current_user:
        raise HTTPException(401, "Authentication required")

    acct = db.query(LinkedInAccount).filter_by(id=account_id, is_active=True).first()
    if not acct:
        raise HTTPException(404, "LinkedIn account not found")

    if body.name is not None:
        acct.name = body.name.strip()
    if body.linkedin_profile_url is not None:
        acct.linkedin_profile_url = body.linkedin_profile_url.strip() or None
    if body.pb_api_key is not None:
        if body.pb_api_key.strip():
            if not ENCRYPTION_AVAILABLE:
                raise HTTPException(500, "Encryption not available on server. Contact admin.")
            acct.pb_api_key_encrypted = encrypt(body.pb_api_key.strip())
        else:
            acct.pb_api_key_encrypted = None
    if body.sales_nav_agent_id is not None:
        acct.sales_nav_agent_id = body.sales_nav_agent_id.strip() or None
    if body.connect_agent_id is not None:
        acct.connect_agent_id = body.connect_agent_id.strip() or None
    if body.message_agent_id is not None:
        acct.message_agent_id = body.message_agent_id.strip() or None
    if body.inbox_scraper_agent_id is not None:
        acct.inbox_scraper_agent_id = body.inbox_scraper_agent_id.strip() or None

    db.commit()
    db.refresh(acct)
    logger.info("Updated LinkedIn account '%s' (id=%s) by user=%s", acct.name, acct.id, current_user.id)
    return _account_to_dict(acct)


@router.delete("/{account_id}")
def delete_linkedin_account(
    account_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-delete a LinkedIn account (set is_active=False)."""
    if not current_user:
        raise HTTPException(401, "Authentication required")

    acct = db.query(LinkedInAccount).filter_by(id=account_id, is_active=True).first()
    if not acct:
        raise HTTPException(404, "LinkedIn account not found")

    acct.is_active = False
    db.commit()
    logger.info("Soft-deleted LinkedIn account '%s' (id=%s)", acct.name, acct.id)
    return {"status": "deleted", "id": account_id}


@router.post("/{account_id}/validate")
def validate_linkedin_account(
    account_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Validate all configured phantom IDs against the PhantomBuster API.

    Returns per-phantom validation results so the user knows immediately
    if a phantom was deleted, renamed, or misconfigured.
    """
    if not current_user:
        raise HTTPException(401, "Authentication required")

    acct = db.query(LinkedInAccount).filter_by(id=account_id, is_active=True).first()
    if not acct:
        raise HTTPException(404, "LinkedIn account not found")

    from ..services.crypto import decrypt
    import httpx as _httpx
    import os as _os

    # Resolve PB API key for this account — use RAW httpx, NOT PhantomBusterClient,
    # so validation calls don't trip the circuit breaker (which protects the worker).
    pb_key = None
    if acct.pb_api_key_encrypted:
        try:
            pb_key = decrypt(acct.pb_api_key_encrypted)
        except Exception as exc:
            return {
                "account_id": account_id,
                "account_name": acct.name,
                "pb_connection": {"status": "error", "detail": f"Decryption failed: {exc}"},
                "phantoms": {},
                "overall": "error",
            }
    if not pb_key:
        pb_key = _os.getenv("PHANTOMBUSTER_API_KEY", "")
    if not pb_key:
        return {
            "account_id": account_id,
            "account_name": acct.name,
            "pb_connection": {"status": "error", "detail": "No PB API key configured"},
            "phantoms": {},
            "overall": "error",
        }

    pb_headers = {"X-Phantombuster-Key": pb_key}

    phantom_fields = {
        "connect": acct.connect_agent_id,
        "message": acct.message_agent_id,
        "sales_nav": acct.sales_nav_agent_id,
        "inbox_scraper": acct.inbox_scraper_agent_id,
    }

    results = {}
    errors = 0
    for label, agent_id in phantom_fields.items():
        if not agent_id:
            results[label] = {"status": "not_configured", "agent_id": None}
            continue
        try:
            r = _httpx.get(
                f"https://api.phantombuster.com/api/v2/agents/fetch?id={agent_id}",
                headers=pb_headers, timeout=15,
            )
            r.raise_for_status()
            info = r.json()
            results[label] = {
                "status": "valid",
                "agent_id": agent_id,
                "name": info.get("name", "?"),
                "script": info.get("script", "?"),
                "launches": info.get("nbLaunches", 0),
            }
        except Exception as exc:
            err_str = str(exc)
            if "404" in err_str:
                results[label] = {"status": "deleted", "agent_id": agent_id, "detail": "Phantom not found in PB workspace"}
            elif "403" in err_str or "401" in err_str:
                results[label] = {"status": "auth_error", "agent_id": agent_id, "detail": "PB API key invalid or insufficient permissions"}
            else:
                results[label] = {"status": "error", "agent_id": agent_id, "detail": err_str[:200]}
            errors += 1

    overall = "healthy" if errors == 0 else ("degraded" if errors < len([v for v in phantom_fields.values() if v]) else "error")

    # Auto-detect LinkedIn account identity from Message Sender result
    # The message phantom's result.json has senderName + senderProfileUrl
    detected_identity = None
    msg_agent_id = acct.message_agent_id
    if msg_agent_id and results.get("message", {}).get("status") == "valid":
        try:
            agent_r = _httpx.get(
                f"https://api.phantombuster.com/api/v2/agents/fetch?id={msg_agent_id}",
                headers=pb_headers, timeout=15,
            )
            agent_data = agent_r.json() if agent_r.status_code == 200 else {}
            org_s3 = agent_data.get("orgS3Folder", "")
            agent_s3 = agent_data.get("s3Folder", "")
            if org_s3 and agent_s3:
                result_url = f"https://phantombuster.s3.amazonaws.com/{org_s3}/{agent_s3}/result.json"
                r = _httpx.get(result_url, timeout=10)
                if r.status_code == 200:
                    rows = r.json()
                    if isinstance(rows, list) and rows:
                        sender_name = rows[0].get("senderName", "")
                        sender_url = rows[0].get("senderProfileUrl", "")
                        if sender_name:
                            detected_identity = {
                                "name": sender_name,
                                "linkedin_url": sender_url,
                            }
                            # Auto-update the account if name is still generic
                            if acct.name in ("Default Account", "Primary Outreach Account") and sender_name:
                                acct.name = sender_name
                                acct.linkedin_profile_url = sender_url or acct.linkedin_profile_url
                                db.commit()
                                logger.info("Auto-updated account '%s' identity from PB: %s", account_id, sender_name)
        except Exception as exc:
            logger.debug("Could not auto-detect identity: %s", exc)

    try:
        pb.close()
    except Exception:
        pass

    return {
        "account_id": account_id,
        "account_name": acct.name,
        "pb_connection": {"status": "ok"},
        "phantoms": results,
        "overall": overall,
        "errors": errors,
        "detected_identity": detected_identity,
    }


@router.post("/validate-all")
def validate_all_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Validate all active accounts at once. Returns per-account health."""
    accounts = db.query(LinkedInAccount).filter(LinkedInAccount.is_active == True).all()
    results = []
    for acct in accounts:
        try:
            # Reuse the single-account validator
            result = validate_linkedin_account(account_id=acct.id, db=db, current_user=current_user)
            results.append(result)
        except Exception as exc:
            results.append({
                "account_id": acct.id,
                "account_name": acct.name,
                "overall": "error",
                "detail": str(exc)[:200],
            })
    return {"accounts": results}


def _detect_linkedin_identity(session_cookie: str, user_agent: str = "") -> dict:
    """Use a LinkedIn session cookie to fetch the account holder's name and profile URL.

    Hits LinkedIn's /voyager/api/me with the li_at cookie to get the mini profile.
    Returns {"name": "...", "linkedin_url": "..."} or empty dict on failure.
    """
    import httpx as _httpx
    if not session_cookie:
        return {}
    try:
        ua = user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        client = _httpx.Client(timeout=15, follow_redirects=True, cookies={"li_at": session_cookie})
        client.get("https://www.linkedin.com", headers={"User-Agent": ua})
        csrf = client.cookies.get("JSESSIONID", "").replace('"', '')
        if not csrf:
            client.close()
            return {}
        r = client.get("https://www.linkedin.com/voyager/api/me", headers={
            "User-Agent": ua,
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-restli-protocol-version": "2.0.0",
            "csrf-token": csrf,
        })
        client.close()
        if r.status_code != 200:
            return {}
        for item in r.json().get("included", []):
            fn = item.get("firstName", "")
            ln = item.get("lastName", "")
            pid = item.get("publicIdentifier", "")
            if fn and ln:
                return {
                    "name": f"{fn} {ln}",
                    "linkedin_url": f"https://www.linkedin.com/in/{pid}" if pid else "",
                }
        return {}
    except Exception:
        return {}


def _auto_sync_from_pb(db: Session) -> dict:
    """Auto-discover all PB agents and create/update LinkedInAccounts.

    Called at startup (main.py) and via the /sync-from-pb endpoint.
    Reads all agents, classifies by script type, groups by session cookie,
    creates one LinkedInAccount per unique identity. Returns sync stats.
    """
    import httpx as _httpx
    import os as _os
    import json as _json
    import base64 as _b64
    import re as _re

    pb_key = _os.getenv("PHANTOMBUSTER_API_KEY", "")
    if not pb_key:
        return {"error": "PHANTOMBUSTER_API_KEY not configured", "synced": 0}

    pb_headers = {"X-Phantombuster-Key": pb_key}

    # 1. Fetch all agents
    try:
        r = _httpx.get("https://api.phantombuster.com/api/v2/agents/fetch-all",
                        headers=pb_headers, timeout=15)
        r.raise_for_status()
        agents = r.json()
    except Exception as exc:
        raise HTTPException(502, f"Failed to fetch PB agents: {exc}")

    # 2. For each agent, fetch full config to get session cookie + classify
    by_member = {}  # member_id -> {connect, message, salesnav, inbox, name, ...}

    for a in agents:
        aid = str(a.get("id", ""))
        script = (a.get("script") or "").lower()
        agent_name = a.get("name", "")

        # Classify phantom type(s) — a phantom can have multiple capabilities.
        # "LinkedIn Outreach.js" handles BOTH connect and message in one phantom.
        ptypes = []
        if "outreach" in script:
            ptypes = ["connect", "message"]  # Outreach phantom does both
        elif "auto connect" in script:
            ptypes = ["connect"]
        elif "message sender" in script:
            ptypes = ["message"]
        elif "sales nav" in script or "sales navigator" in script:
            ptypes = ["salesnav"]
        elif "inbox" in script:
            ptypes = ["inbox"]
        else:
            continue  # skip irrelevant agents (export, scraper, etc.)

        # Fetch full agent config to get session cookie
        try:
            ar = _httpx.get(
                f"https://api.phantombuster.com/api/v2/agents/fetch?id={aid}",
                headers=pb_headers, timeout=15,
            )
            agent_data = ar.json() if ar.status_code == 200 else {}
        except Exception:
            continue

        arg = {}
        raw_arg = agent_data.get("argument")
        if raw_arg:
            try:
                arg = _json.loads(raw_arg) if isinstance(raw_arg, str) else raw_arg
            except Exception:
                pass

        # Session cookie can be in top-level arg OR in the identities array
        # (PB uses identities[] for browser-extension authenticated phantoms like Sales Nav)
        cookie = arg.get("sessionCookie", "")
        user_agent = arg.get("userAgent", "")
        if not cookie:
            identities = arg.get("identities", [])
            if identities and isinstance(identities, list):
                cookie = identities[0].get("sessionCookie", "")
                user_agent = identities[0].get("userAgent", user_agent)
        # Use first 30 chars of cookie as identity key — different cookies = different accounts.
        identity_key = cookie[:30] if cookie else f"no_cookie_{aid}"

        if identity_key not in by_member:
            by_member[identity_key] = {
                "connect_agent_id": None,
                "message_agent_id": None,
                "sales_nav_agent_id": None,
                "inbox_scraper_agent_id": None,
                "agent_names": [],
            }

        entry = by_member[identity_key]
        # Store cookie + user agent for identity detection later
        if cookie and not entry.get("_cookie"):
            entry["_cookie"] = cookie
            entry["_user_agent"] = user_agent
        # Assign agent ID for each capability this phantom provides
        for ptype in ptypes:
            key = f"{ptype}_agent_id" if ptype != "salesnav" else "sales_nav_agent_id"
            if not entry.get(key):
                entry[key] = aid
        entry["agent_names"].append(agent_name)

    # 3. Create/update LinkedInAccounts
    created = 0
    updated = 0
    results = []

    for member_id, data in by_member.items():
        # Check if account already exists with any of these agent IDs
        existing = None
        for field in ["connect_agent_id", "message_agent_id", "sales_nav_agent_id", "inbox_scraper_agent_id"]:
            val = data.get(field)
            if val:
                match = db.query(LinkedInAccount).filter(
                    getattr(LinkedInAccount, field) == val,
                    LinkedInAccount.is_active == True,
                ).first()
                if match:
                    existing = match
                    break

        if existing:
            # Update missing phantom IDs
            changed = False
            for field in ["connect_agent_id", "message_agent_id", "sales_nav_agent_id", "inbox_scraper_agent_id"]:
                if data.get(field) and not getattr(existing, field):
                    setattr(existing, field, data[field])
                    changed = True
            # Auto-detect name if still generic
            if existing.name.startswith("LinkedIn Account") or existing.name in ("Default Account", "Primary Outreach Account", "PB Account"):
                identity = _detect_linkedin_identity(data.get("_cookie", ""), data.get("_user_agent", ""))
                if identity.get("name"):
                    existing.name = identity["name"]
                    existing.linkedin_profile_url = identity.get("linkedin_url") or existing.linkedin_profile_url
                    changed = True
            if changed:
                db.commit()
                updated += 1
            results.append({"name": existing.name, "id": existing.id, "action": "updated" if changed else "unchanged"})
        else:
            # Create new account
            # Auto-detect LinkedIn identity from session cookie
            identity = _detect_linkedin_identity(data.get("_cookie", ""), data.get("_user_agent", ""))
            name = identity.get("name") or f"LinkedIn Account {created + 1}"
            li_url = identity.get("linkedin_url", "")
            acct = LinkedInAccount(
                user_id=None,  # system-level, not user-scoped
                name=name,
                linkedin_profile_url=li_url or None,
                connect_agent_id=data.get("connect_agent_id"),
                message_agent_id=data.get("message_agent_id"),
                sales_nav_agent_id=data.get("sales_nav_agent_id"),
                inbox_scraper_agent_id=data.get("inbox_scraper_agent_id"),
                is_active=True,
            )
            db.add(acct)
            db.commit()
            db.refresh(acct)
            created += 1
            results.append({"name": name, "id": acct.id, "action": "created"})

    logger.info("PB auto-sync: synced=%d created=%d updated=%d", len(results), created, updated)
    return {
        "synced": len(results),
        "created": created,
        "updated": updated,
        "accounts": results,
    }


@router.post("/sync-from-pb")
def sync_from_phantombuster(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger manual re-sync of LinkedIn accounts from PhantomBuster."""
    if not current_user:
        raise HTTPException(401, "Authentication required")
    return _auto_sync_from_pb(db)
