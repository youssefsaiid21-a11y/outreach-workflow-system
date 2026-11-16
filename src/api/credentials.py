"""CRUD API for per-user external service credentials (PhantomBuster, etc.)."""
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db.database import get_db
from ..db.models import UserCredential, User
from ..services.crypto import encrypt, decrypt, ENCRYPTION_AVAILABLE
from .auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/credentials", tags=["credentials"])

ALLOWED_SERVICES = {
    "phantombuster": ["api_key", "connect_agent_id", "message_agent_id", "list_id", "identity_id"],
}


@router.get("/{service}")
def get_credentials(service: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List credential key names for the current user (values masked)."""
    if not current_user:
        raise HTTPException(401, "Authentication required")
    if service not in ALLOWED_SERVICES:
        raise HTTPException(400, f"Unknown service: {service}")
    rows = db.query(UserCredential).filter_by(user_id=current_user.id, service=service).all()
    result = {}
    for r in rows:
        val = ""
        try:
            val = decrypt(r.encrypted_value)
        except Exception:
            val = "***decryption-error***"
        # Mask: show only last 4 chars
        if len(val) > 8:
            masked = "****" + val[-4:]
        else:
            masked = "****"
        result[r.key_name] = masked
    return {"service": service, "keys": result, "configured": bool(rows)}


@router.put("/{service}")
def upsert_credentials(service: str, body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Upsert credentials for a service. Body: {"api_key": "value", ...}"""
    if not current_user:
        raise HTTPException(401, "Authentication required")
    if service not in ALLOWED_SERVICES:
        raise HTTPException(400, f"Unknown service: {service}")
    if not ENCRYPTION_AVAILABLE:
        raise HTTPException(500, "Encryption not available on server. Contact admin.")
    allowed_keys = ALLOWED_SERVICES[service]
    saved = []
    for key_name, value in body.items():
        if key_name not in allowed_keys:
            continue
        value = (value or "").strip()
        if not value:
            # Delete if empty
            db.query(UserCredential).filter_by(
                user_id=current_user.id, service=service, key_name=key_name
            ).delete()
            continue
        existing = db.query(UserCredential).filter_by(
            user_id=current_user.id, service=service, key_name=key_name
        ).first()
        if existing:
            existing.encrypted_value = encrypt(value)
        else:
            cred = UserCredential(
                user_id=current_user.id,
                service=service,
                key_name=key_name,
                encrypted_value=encrypt(value),
            )
            db.add(cred)
        saved.append(key_name)
    db.commit()
    return {"saved": saved}


@router.delete("/{service}")
def delete_credentials(service: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Remove all credentials for a service."""
    if not current_user:
        raise HTTPException(401, "Authentication required")
    if service not in ALLOWED_SERVICES:
        raise HTTPException(400, f"Unknown service: {service}")
    count = db.query(UserCredential).filter_by(user_id=current_user.id, service=service).delete()
    db.commit()
    return {"deleted": count}


@router.post("/{service}/test")
def test_credentials(service: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Test if stored credentials are valid."""
    if not current_user:
        raise HTTPException(401, "Authentication required")
    if service != "phantombuster":
        raise HTTPException(400, "Test only supported for phantombuster")
    row = db.query(UserCredential).filter_by(
        user_id=current_user.id, service="phantombuster", key_name="api_key"
    ).first()
    if not row:
        return {"ok": False, "error": "No API key configured"}
    try:
        api_key = decrypt(row.encrypted_value)
        r = httpx.get(
            "https://api.phantombuster.com/api/v2/orgs/fetch",
            headers={"X-Phantombuster-Key": api_key},
            timeout=10,
        )
        if r.status_code == 200:
            return {"ok": True, "detail": "Connected to PhantomBuster"}
        return {"ok": False, "error": f"PB returned {r.status_code}"}
    except Exception as e:
        logger.error("Credential test failed for service=%s user=%s: %s", service, current_user.id, e)
        return {"ok": False, "error": "Connection test failed"}


def get_user_pb_credentials(db: Session, user_id: str) -> dict | None:
    """Helper: fetch decrypted PB credentials for a user. Returns None if not configured."""
    rows = db.query(UserCredential).filter_by(user_id=user_id, service="phantombuster").all()
    if not rows:
        return None
    creds = {}
    for r in rows:
        try:
            creds[r.key_name] = decrypt(r.encrypted_value)
        except Exception:
            logger.warning("Failed to decrypt PB credential %s for user %s", r.key_name, user_id)
    if not creds.get("api_key"):
        return None
    return creds
