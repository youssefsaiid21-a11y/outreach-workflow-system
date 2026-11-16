"""
Authentication and user management for the Example outreach system.

Supports three authentication methods (checked in priority order):
  1. JWT bearer tokens — used by the dashboard SPA after login. Tokens are
     HS256-signed with JWT_SECRET, expire after 72 hours.
  2. X-Api-Key header — used by CLI tools and external integrations. Two formats:
     a) Per-user: `<user_id>.<secret>` — secret is bcrypt-hashed in users.api_key_hash
     b) Legacy: raw API_KEY env var — bound to the first admin user (deprecated)
  3. __session cookie — legacy browser session (superseded by JWT in the SPA)

Auth is ALWAYS enforced (SWAT-H2/H3 hardening). No "first-run auth disabled" path.
New users register via POST /api/auth/register (first user becomes admin).

This module also provides:
  - Password change / admin reset
  - "Forgot password" flow with email-based reset links (Graph API → SMTP fallback)
  - Rate limiting on auth endpoints (5 registrations/min, 10 logins/min)

Related files:
  - main.py           — derives the session token from API_KEY and calls set_session_token()
  - src/api/router.py — auth_router is included WITHOUT the global auth dependency
                         (login/register must be publicly accessible)
  - src/db/models.py  — User model (password_hash, api_key_hash, role)
"""
import hmac
import html
import os
import uuid
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, Depends, Header, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# Endpoint-specific rate limiter for registration (stricter than global default)
_auth_limiter = Limiter(key_func=get_remote_address)

# ── JWT configuration ────────────────────────────────────────────────────────
# JWT_SECRET is the HMAC key for signing/verifying JWTs. In production, set a
# dedicated random secret (e.g. `openssl rand -hex 32`). In development, an
# ephemeral key is generated — sessions won't survive restarts, which is fine.
_JWT_SECRET = os.getenv("JWT_SECRET")
if not _JWT_SECRET:
    _env = os.getenv("ENV", "development").lower()
    if _env not in ("development", "dev", "test"):
        raise RuntimeError(
            "JWT_SECRET is required in production. "
            "Generate one with: openssl rand -hex 32"
        )
    import secrets as _secrets
    _JWT_SECRET = _secrets.token_urlsafe(48)
    logger.warning("JWT_SECRET not set  -  using ephemeral key. Set JWT_SECRET in .env for stable sessions.")

_JWT_ALGORITHM = "HS256"
_JWT_EXPIRY_HOURS = 72  # 3 days
_RESET_TOKEN_EXPIRY_MINUTES = 30  # password reset links expire in 30 min

# Module-level session token set at startup by main.py. This is the HMAC-derived
# token injected into the __session cookie by the SPA middleware. It provides
# legacy browser authentication before JWT was introduced.
_session_token: str | None = None


def set_session_token(token: str) -> None:
    """Called by main.py at startup to inject the session token derived from API_KEY."""
    global _session_token
    _session_token = token


# ── Password hashing ─────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── JWT helpers ──────────────────────────────────────────────────────────────

def _create_token(user_id: str, email: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    return jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])


# ── FastAPI dependencies ─────────────────────────────────────────────────────

_LEGACY_API_KEY_WARNED = False


def _resolve_api_key(x_api_key: str, db: Session) -> Optional[User]:
    """
    FIX-2: Resolve an X-Api-Key header into a specific User.

    Two formats are accepted:

      1. ``<user_id>.<secret>``  -  per-user API keys, the secret is bcrypt
         hashed in the ``users.api_key_hash`` column. This is the only safe
         multi-user format.

      2. Legacy env-var key  -  when the X-Api-Key value matches the global
         ``API_KEY`` env var, we bind the caller to a deterministic
         "service" user: the first admin sorted by created_at. A deprecation
         warning is logged once per process.

    Returns the resolved User, or None if no match. Never raises.
    """
    global _LEGACY_API_KEY_WARNED

    if not x_api_key:
        return None

    # Format 1: per-user `<user_id>.<secret>`
    if "." in x_api_key:
        try:
            user_id, secret = x_api_key.split(".", 1)
        except ValueError:
            return None
        if user_id and secret:
            user = db.query(User).filter_by(id=user_id).first()
            if user and getattr(user, "api_key_hash", None):
                try:
                    if bcrypt.checkpw(secret.encode(), user.api_key_hash.encode()):
                        return user
                except (ValueError, TypeError):
                    return None

    # Format 2: legacy global env-var key bound to the service admin user.
    expected_key = os.getenv("API_KEY")
    if expected_key and hmac.compare_digest(x_api_key, expected_key):
        admin = (
            db.query(User)
            .filter_by(role=UserRole.admin)
            .order_by(User.created_at.asc())
            .first()
        )
        if admin and not _LEGACY_API_KEY_WARNED:
            logger.warning(
                "DEPRECATION: legacy API_KEY env var used by X-Api-Key header. "
                "Bound to service user %s. Migrate CLI/agent integrations to "
                "per-user `<user_id>.<secret>` keys.",
                admin.email,
            )
            _LEGACY_API_KEY_WARNED = True
        return admin
    return None


def get_current_user(
    authorization: str = Header(None),
    x_api_key: str = Header(None),
    __session: str = Cookie(None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Returns the authenticated User, or None if auth is disabled.

    Priority:
    1. Authorization: Bearer <jwt> header
    2. X-Api-Key header  -  per-user `<user_id>.<secret>` or legacy env-var key
       bound to the service admin user (FIX-2).
    3. __session cookie (legacy  -  only when no users exist)
    4. If no API_KEY set and no users exist → auth disabled (returns None)
    """
    # 1. JWT bearer token
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            try:
                payload = _decode_token(token)
                user = db.query(User).filter_by(id=payload["sub"]).first()
                if user:
                    return user
            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                pass
            raise HTTPException(status_code=401, detail="Invalid or expired token.")

    # 2. X-Api-Key header  -  per-user or legacy env var
    if x_api_key:
        resolved = _resolve_api_key(x_api_key, db)
        if resolved is not None:
            return resolved

    # SWAT-H2/H3: auth is ALWAYS enforced. No "first-run auth disabled" path.
    # Register via POST /api/auth/register (which doesn't require auth).
    raise HTTPException(status_code=401, detail="Authentication required. Please log in.")


def require_api_key(
    authorization: str = Header(None),
    x_api_key: str = Header(None),
    __session: str = Cookie(None),
    db: Session = Depends(get_db),
):
    """Auth-only dependency (no user object returned). Used by router.py as the
    default dependency for all /api/* endpoints. Routes that need the actual User
    object should use get_current_user instead."""
    # If JWT token provided, validate it
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            try:
                _decode_token(token)
                return
            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                raise HTTPException(status_code=401, detail="Invalid or expired token.")

    # Per-user or legacy X-Api-Key (FIX-2)
    if x_api_key:
        # Check legacy env-var key first (no DB needed) for backward compat
        expected_legacy = os.getenv("API_KEY")
        if expected_legacy and hmac.compare_digest(x_api_key, expected_legacy):
            return
        # Per-user key (requires DB lookup)
        resolved = _resolve_api_key(x_api_key, db)
        if resolved is not None:
            return

    # SWAT-H2/H3: auth is ALWAYS enforced. No "first-run auth disabled" path.
    raise HTTPException(status_code=401, detail="Invalid or missing API key.")


# ── Auth endpoints ───────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/register")
@_auth_limiter.limit("5/minute")
def register(request: Request, req: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new BD rep account. First user becomes admin."""
    email = req.email.strip().lower()
    if len(req.password) < 12:
        raise HTTPException(status_code=422, detail="Password must be at least 12 characters.")
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="Name is required.")

    existing = db.query(User).filter_by(email=email).first()
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    # First user is admin
    user_count = db.query(User).count()
    role = UserRole.admin if user_count == 0 else UserRole.rep

    user = User(
        id=str(uuid.uuid4()),
        name=req.name.strip(),
        email=email,
        password_hash=_hash_password(req.password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = _create_token(user.id, user.email, user.role.value)
    logger.info(f"User registered: {user.email} (role={user.role.value})")

    return {
        "token": token,
        "user": _user_to_dict(user),
    }


@router.post("/login")
@_auth_limiter.limit("10/minute")
def login(request: Request, req: LoginRequest, db: Session = Depends(get_db)):
    """Log in with email + password. Returns a JWT token."""
    email = req.email.strip().lower()
    user = db.query(User).filter_by(email=email).first()
    if not user or not _verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token = _create_token(user.id, user.email, user.role.value)
    logger.info(f"User logged in: {user.email}")

    return {
        "token": token,
        "user": _user_to_dict(user),
    }


class ChangePasswordRequest(BaseModel):
    email: EmailStr
    current_password: str
    new_password: str


class AdminResetPasswordRequest(BaseModel):
    email: EmailStr
    new_password: str


def _change_pw_keyfunc(request: Request) -> str:
    """FIX-18: rate-limit change-password by (IP, email) so an attacker can't
    burn an account's lockout from a single IP and pivot to another."""
    try:
        body = request.scope.get("_change_pw_email") or ""
    except Exception:
        body = ""
    return f"{get_remote_address(request)}::{body}"


@router.post("/change-password")
@_auth_limiter.limit("5/minute", key_func=_change_pw_keyfunc)
def change_password(
    request: Request,
    req: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change password for an existing account.

    FIX-18: requires an authenticated session AND the current password.
    The session must belong to the same user whose password is being changed
    (admins must use /admin-reset-password instead).
    """
    email = req.email.strip().lower()
    request.scope["_change_pw_email"] = email  # surfaced to the rate-limiter keyfunc
    if len(req.new_password) < 12:
        raise HTTPException(status_code=422, detail="Password must be at least 12 characters.")

    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if (current_user.email or "").lower() != email:
        raise HTTPException(status_code=403, detail="You can only change your own password.")

    user = db.query(User).filter_by(email=email).first()
    if not user or not _verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    user.password_hash = _hash_password(req.new_password)
    db.commit()
    logger.info(f"Password changed for: {email}")

    token = _create_token(user.id, user.email, user.role.value)
    return {
        "token": token,
        "user": _user_to_dict(user),
    }


@router.post("/admin-reset-password")
def admin_reset_password(
    req: AdminResetPasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin-only: reset any user's password without knowing the current one."""
    if not current_user or current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin privileges required.")

    email = req.email.strip().lower()
    if len(req.new_password) < 12:
        raise HTTPException(status_code=422, detail="Password must be at least 12 characters.")

    user = db.query(User).filter_by(email=email).first()
    if not user:
        raise HTTPException(status_code=404, detail="No account found with this email.")

    user.password_hash = _hash_password(req.new_password)
    db.commit()
    logger.info(f"Admin password reset for: {email} (by {current_user.email})")

    return {
        "message": f"Password reset successfully for {email}.",
        "user": _user_to_dict(user),
    }


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    """Returns the currently logged-in user."""
    if not user:
        return {"user": None, "auth_required": False}
    return {"user": _user_to_dict(user)}


@router.get("/users")
def list_users(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List all registered users. Admin only."""
    if not current_user or current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    users = db.query(User).order_by(User.created_at).all()
    return [_user_to_dict(u) for u in users]


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "role": u.role.value if u.role else "rep",
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


# ── Password reset via email ────────────────────────────────────────────────
# Reset flow:
#   1. User POSTs /api/auth/forgot-password with their email
#   2. Server generates a short-lived JWT (30 min) with purpose="password_reset"
#   3. Server sends the reset link via Graph API (preferred) or SMTP (fallback)
#   4. User clicks link → GET /api/auth/reset-password renders an HTML form
#   5. Form POSTs /api/auth/reset-password with the token + new password
#   6. Server validates the token and updates the password hash
# Anti-enumeration: forgot-password always returns 200 regardless of email existence.

def _create_reset_token(user_id: str, email: str) -> str:
    """Create a short-lived JWT for password reset."""
    payload = {
        "sub": user_id,
        "email": email,
        "purpose": "password_reset",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=_RESET_TOKEN_EXPIRY_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def _decode_reset_token(token: str) -> dict:
    """Decode and validate a password reset token."""
    payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
    if payload.get("purpose") != "password_reset":
        raise jwt.InvalidTokenError("Not a password reset token.")
    return payload


def _build_reset_html(user_name: str, reset_link: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 500px; margin: 0 auto; padding: 40px 20px; color: #1a1a2e;">
  <div style="text-align: center; margin-bottom: 30px;">
    <h2 style="margin: 0; color: #1a1a2e;">Example Outreach</h2>
  </div>
  <p>Hi {user_name},</p>
  <p>You requested a password reset for your account.</p>
  <div style="text-align: center; margin: 30px 0;">
    <a href="{reset_link}"
       style="display: inline-block; background: #1a1a2e; color: #ffffff; text-decoration: none;
              padding: 14px 32px; border-radius: 8px; font-weight: 600; font-size: 15px;">
      Reset Password
    </a>
  </div>
  <p style="color: #666; font-size: 13px;">
    This link expires in {_RESET_TOKEN_EXPIRY_MINUTES} minutes. If you didn't request this, ignore this email.
  </p>
  <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
  <p style="color: #999; font-size: 12px; text-align: center;">Example Outreach</p>
</body>
</html>"""


def _send_reset_email_graph(to_email: str, html_body: str) -> bool:
    """Send password reset email from demo-member-005@company.example via Graph API.

    Uses client credentials flow (app-only token) so no user token is
    needed.  Sends via /users/demo-member-005@company.example/sendMail.

    Requires:
      - MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET, MICROSOFT_TENANT_ID in .env
      - Mail.Send application permission with admin consent on the app registration
    """
    import httpx
    import msal

    client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
    client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "").strip()
    from_mailbox = os.getenv("SMTP_FROM", "demo-member-005@company.example").strip()

    if not all([client_id, client_secret, tenant_id]):
        logger.debug("Graph client credentials not configured")
        return False

    try:
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            logger.warning("Graph client credentials auth failed: %s", result.get("error_description", result))
            return False

        token = result["access_token"]
        resp = httpx.post(
            f"https://graph.microsoft.com/v1.0/users/{from_mailbox}/sendMail",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "message": {
                    "subject": "Reset your Example Outreach password",
                    "body": {"contentType": "HTML", "content": html_body},
                    "toRecipients": [{"emailAddress": {"address": to_email}}],
                },
                "saveToSentItems": False,
            },
            timeout=15,
        )
        if resp.status_code in (200, 202):
            logger.info("Password reset email sent to %s from %s via Graph API", to_email, from_mailbox)
            return True
        else:
            logger.warning("Graph sendMail returned %d: %s", resp.status_code, resp.text[:300])
            return False
    except Exception as exc:
        logger.warning("Graph client credentials send failed: %s", exc)
        return False


def _send_reset_email(to_email: str, reset_link: str, user_name: str) -> bool:
    """Send password reset email. Tries Graph API first, falls back to SMTP."""
    html_body = _build_reset_html(user_name, reset_link)

    # 1. Try Microsoft Graph API (no SMTP AUTH needed)
    if _send_reset_email_graph(to_email, html_body):
        return True

    # 2. Fall back to SMTP
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()
    smtp_from = os.getenv("SMTP_FROM", smtp_user).strip()

    if all([smtp_host, smtp_user, smtp_pass]):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Reset your Example Outreach password"
        msg["From"] = smtp_from
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            logger.info("Password reset email sent to %s via SMTP", to_email)
            return True
        except Exception as e:
            logger.error("SMTP send failed for %s: %s", to_email, e)

    # 3. Last resort: log the link
    logger.error("All email methods failed for %s", to_email)
    logger.info("PASSWORD RESET LINK (fallback) for %s: %s", to_email, reset_link)
    return False


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    """
    Request a password reset link. Sends an email with a time-limited reset link.
    Always returns 200 to prevent email enumeration attacks.
    """
    email = req.email.strip().lower()
    user = db.query(User).filter_by(email=email).first()

    if not user:
        # Don't reveal whether the email exists
        return {"message": "If an account with that email exists, a reset link has been sent."}

    token = _create_reset_token(user.id, user.email)

    # Build reset link using PUBLIC_HOST if set, else request's base URL
    public_host = os.getenv("PUBLIC_HOST", "").strip()
    port = os.getenv("PORT", "3004").strip()
    if public_host:
        base_url = f"http://{public_host}:{port}"
    else:
        base_url = str(request.base_url).rstrip("/")
    reset_link = f"{base_url}/api/auth/reset-password?token={token}"

    _send_reset_email(user.email, reset_link, user.name)

    return {"message": "If an account with that email exists, a reset link has been sent."}


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(token: str = ""):
    """Renders the password reset form. The token is embedded in the form."""
    # Validate token is present (detailed validation happens on submit)
    if not token:
        return HTMLResponse(
            content="<html><body><h2>Invalid or missing reset link.</h2></body></html>",
            status_code=400,
        )

    # Verify token is still valid before showing the form
    try:
        _decode_reset_token(token)
    except jwt.ExpiredSignatureError:
        return HTMLResponse(content=_reset_page_html("", error="This reset link has expired. Please request a new one."), status_code=400)
    except jwt.InvalidTokenError:
        return HTMLResponse(content=_reset_page_html("", error="Invalid reset link."), status_code=400)

    return HTMLResponse(content=_reset_page_html(html.escape(token, quote=True)))


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/reset-password")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Set a new password using a valid reset token."""
    if len(req.new_password) < 12:
        raise HTTPException(status_code=422, detail="Password must be at least 12 characters.")

    try:
        payload = _decode_reset_token(req.token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Reset link has expired. Please request a new one.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid reset token.")

    user = db.query(User).filter_by(id=payload["sub"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.password_hash = _hash_password(req.new_password)
    db.commit()

    logger.info("Password reset via email link for: %s", user.email)

    token = _create_token(user.id, user.email, user.role.value)
    return {
        "message": "Password reset successfully. You can now log in.",
        "token": token,
        "user": _user_to_dict(user),
    }


def _reset_page_html(token: str, error: str = "") -> str:
    """Self-contained HTML page for password reset."""
    safe_error = html.escape(error) if error else ""
    error_html = f'<div style="color:#e74c3c;background:#ffeaea;padding:12px;border-radius:8px;margin-bottom:20px;font-size:14px;">{safe_error}</div>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Reset Password  -  Example Outreach</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .card {{
      background: #fff;
      border-radius: 16px;
      padding: 40px;
      width: 100%;
      max-width: 420px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.3);
    }}
    h1 {{
      font-size: 22px;
      color: #1a1a2e;
      margin-bottom: 8px;
    }}
    .subtitle {{
      color: #666;
      font-size: 14px;
      margin-bottom: 28px;
    }}
    label {{
      display: block;
      font-weight: 600;
      font-size: 13px;
      color: #333;
      margin-bottom: 6px;
    }}
    input {{
      width: 100%;
      padding: 12px 14px;
      border: 2px solid #e0e0e0;
      border-radius: 8px;
      font-size: 15px;
      transition: border-color 0.2s;
      outline: none;
      margin-bottom: 16px;
    }}
    input:focus {{
      border-color: #1a1a2e;
    }}
    button {{
      width: 100%;
      padding: 14px;
      background: #1a1a2e;
      color: #fff;
      border: none;
      border-radius: 8px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
    }}
    button:hover {{ background: #16213e; }}
    button:disabled {{ background: #999; cursor: not-allowed; }}
    .msg {{
      margin-top: 16px;
      padding: 12px;
      border-radius: 8px;
      font-size: 14px;
      display: none;
    }}
    .msg.ok {{ display: block; color: #27ae60; background: #eafaf1; }}
    .msg.err {{ display: block; color: #e74c3c; background: #ffeaea; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Reset Password</h1>
    <p class="subtitle">Enter your new password below.</p>
    {error_html}
    <form id="resetForm" {"style='display:none;'" if error else ""}>
      <label for="pw1">New password</label>
      <input type="password" id="pw1" placeholder="At least 12 characters" minlength="12" required />
      <label for="pw2">Confirm password</label>
      <input type="password" id="pw2" placeholder="Re-enter password" minlength="12" required />
      <button type="submit" id="btn">Set New Password</button>
    </form>
    <div class="msg" id="msg"></div>
  </div>
  <script>
    const form = document.getElementById('resetForm');
    const msg = document.getElementById('msg');
    const btn = document.getElementById('btn');

    form.addEventListener('submit', async (e) => {{
      e.preventDefault();
      const pw1 = document.getElementById('pw1').value;
      const pw2 = document.getElementById('pw2').value;

      if (pw1.length < 6) {{
        msg.className = 'msg err';
        msg.textContent = 'Password must be at least 6 characters.';
        return;
      }}
      if (pw1 !== pw2) {{
        msg.className = 'msg err';
        msg.textContent = 'Passwords do not match.';
        return;
      }}

      btn.disabled = true;
      btn.textContent = 'Resetting...';
      msg.style.display = 'none';

      try {{
        const resp = await fetch('/api/auth/reset-password', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ token: '{token}', new_password: pw1 }}),
        }});
        const data = await resp.json();
        if (resp.ok) {{
          msg.className = 'msg ok';
          msg.textContent = 'Password reset successfully! Redirecting to login...';
          form.style.display = 'none';
          if (data.token) {{
            localStorage.setItem('auth_token', data.token);
          }}
          setTimeout(() => window.location.href = '/', 2000);
        }} else {{
          msg.className = 'msg err';
          msg.textContent = data.detail || 'Reset failed. Please try again.';
          btn.disabled = false;
          btn.textContent = 'Set New Password';
        }}
      }} catch (err) {{
        msg.className = 'msg err';
        msg.textContent = 'Network error. Please try again.';
        btn.disabled = false;
        btn.textContent = 'Set New Password';
      }}
    }});
  </script>
</body>
</html>"""
