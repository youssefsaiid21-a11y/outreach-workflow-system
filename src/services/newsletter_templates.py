"""
Newsletter HTML template engine — renders newsletter content into branded HTML emails.

Part of Example's newsletter pipeline. This module is responsible for:

  1. **HTML rendering**: Converts plain-text newsletter body (with paragraph breaks)
     into a branded HTML email using the "Field Notes" template — Example navy/blue
     accent colors, Poppins headings (via Google Fonts), Segoe UI body text.

  2. **Unsubscribe tokens**: Generates and verifies HMAC-SHA256 tokens for one-click
     unsubscribe links. Each recipient gets a unique token so the public unsubscribe
     endpoint (no auth required) can validate that the link hasn't been forged.

  3. **Personalization**: Replaces {{name}} and {{company}} placeholders in the body
     text with per-recipient values before rendering.

  4. **Template structure**: The email uses a 600px table-based layout for maximum
     email client compatibility (Outlook, Gmail, Apple Mail). Includes MSO conditionals
     for Outlook desktop rendering quirks.

Relationships:
  - Called by newsletter_sender.py (for actual sends) and newsletters.py (for previews)
  - Reads Newsletter content_json from the DB (body_text field or legacy block format)
  - HMAC secret comes from NEWSLETTER_HMAC_SECRET or JWT_SECRET env vars
  - Unsubscribe URL configured via NEWSLETTER_UNSUBSCRIBE_BASE_URL or BASE_URL env var
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import html
import logging
import os
import urllib.parse
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import Newsletter, User

logger = logging.getLogger(__name__)

# ── Brand constants (Example brand) ─────────────────────────────────────

NAVY = "#1e3a5f"
NAVY_LIGHT = "#2a4d76"
BLUE_ACCENT = "#0ea5e9"
WHITE = "#FFFFFF"
BG_BODY = "#f8fafc"
TEXT_BODY = "#1e293b"
TEXT_SECONDARY = "#475569"
TEXT_MUTED = "#94a3b8"
BORDER = "#e2e8f0"

# ── Config from env ───────────────────────────────────────────────────────

COMPANY_NAME = os.getenv("NEWSLETTER_COMPANY_NAME", "Example AI")
COMPANY_TAGLINE = os.getenv(
    "NEWSLETTER_COMPANY_TAGLINE",
    "AI-Powered Safety for High-Risk Industries",
)
UNSUBSCRIBE_BASE_URL = os.getenv(
    "NEWSLETTER_UNSUBSCRIBE_BASE_URL",
    os.getenv("BASE_URL", "http://localhost:3004") + "/api/newsletters/unsubscribe",
)
LOGO_URL = os.getenv(
    "NEWSLETTER_LOGO_URL",
    os.getenv("BASE_URL", "http://localhost:3004") + "/assets/portfolio-logo.svg",
)


def _get_hmac_secret() -> str:
    """Retrieve the shared secret used to sign unsubscribe tokens.

    Falls back to JWT_SECRET so existing deployments work without adding
    a new env var. Raises at runtime (not import time) so the app can
    still start for preview/compose flows that don't need tokens.
    """
    secret = os.getenv("NEWSLETTER_HMAC_SECRET") or os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "NEWSLETTER_HMAC_SECRET or JWT_SECRET must be set."
        )
    return secret


if "localhost" in UNSUBSCRIBE_BASE_URL:
    logger.warning(
        "UNSUBSCRIBE_BASE_URL contains 'localhost' (%s). "
        "Unsubscribe links in sent newsletters will not work for recipients. "
        "Set BASE_URL or NEWSLETTER_UNSUBSCRIBE_BASE_URL in .env.",
        UNSUBSCRIBE_BASE_URL,
    )

if not (os.getenv("NEWSLETTER_HMAC_SECRET") or os.getenv("JWT_SECRET")):
    logger.warning(
        "Neither NEWSLETTER_HMAC_SECRET nor JWT_SECRET is set. "
        "Newsletter unsubscribe links will fail at runtime."
    )


def generate_unsubscribe_token(email: str) -> str:
    """Create an HMAC-SHA256 hex token for the given email.

    This token is included in every newsletter's unsubscribe link.
    When the recipient clicks the link, the public /unsubscribe endpoint
    verifies the token to confirm the request is legitimate (no auth needed).
    """
    secret = _get_hmac_secret()
    return _hmac.new(secret.encode(), email.encode(), hashlib.sha256).hexdigest()


def verify_unsubscribe_token(email: str, token: str) -> bool:
    """Constant-time comparison of the provided token against the expected HMAC.

    Uses hmac.compare_digest to prevent timing attacks that could let
    an attacker forge unsubscribe tokens for arbitrary emails.
    """
    expected = generate_unsubscribe_token(email)
    return _hmac.compare_digest(expected, token)


def build_unsubscribe_url(email: str) -> str:
    """Build the full unsubscribe URL with HMAC token and URL-encoded email.

    This URL is embedded in the footer of every newsletter email.
    The email is URL-encoded to handle special characters safely.
    """
    token = generate_unsubscribe_token(email)
    base = UNSUBSCRIBE_BASE_URL
    return "{base}?token={token}&email={email}".format(
        base=base,
        token=token,
        email=urllib.parse.quote(email, safe=""),
    )


# ── Fixed email template ─────────────────────────────────────────────────

def _field_notes_template(
    subject: str,
    body_html: str,
    unsubscribe_url: str,
    recipient_name: str = "",
) -> str:
    """Render the minimal Example newsletter template.

    Clean, personal-email feel with subtle branding. Poppins headings,
    Segoe UI body text, Example navy + blue accent colors.
    """
    safe_subject = html.escape(subject)
    safe_unsub = html.escape(unsubscribe_url, quote=True)
    safe_logo = html.escape(LOGO_URL, quote=True)
    safe_company = html.escape(COMPANY_NAME)
    safe_tagline = html.escape(COMPANY_TAGLINE)

    # Poppins via Google Fonts (fallback to Segoe UI for clients that block it)
    font_head = "Poppins, 'Segoe UI', Helvetica, Arial, sans-serif"
    font_body = "'Segoe UI', Helvetica, Arial, sans-serif"

    return (
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:o="urn:schemas-microsoft-com:office:office">\n'
        '<head>\n'
        '<meta charset="utf-8"/>\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0"/>\n'
        '<title>{subject}</title>\n'
        '<!--[if gte mso 9]>\n'
        '<xml><o:OfficeDocumentSettings><o:AllowPNG/>'
        '<o:PixelsPerInch>96</o:PixelsPerInch>'
        '</o:OfficeDocumentSettings></xml>\n'
        '<![endif]-->\n'
        '<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@500;600;700&display=swap" rel="stylesheet" />\n'
        '<style type="text/css">\n'
        'body, table, td, a {{ margin:0; padding:0; }}\n'
        'table {{ border-collapse:collapse; mso-table-lspace:0pt; mso-table-rspace:0pt; }}\n'
        'img {{ border:0; line-height:100%%; outline:none; text-decoration:none; -ms-interpolation-mode:bicubic; }}\n'
        'body {{ width:100%% !important; -webkit-text-size-adjust:100%%; -ms-text-size-adjust:100%%; }}\n'
        'a {{ color:{blue_accent}; }}\n'
        '</style>\n'
        '</head>\n'
        '<body style="margin:0;padding:0;background-color:{bg_body};'
        'font-family:{font_body};font-size:15px;'
        'color:{text_body};line-height:1.7;">\n'
        '\n'
        '<!-- Outer wrapper -->\n'
        '<table border="0" cellpadding="0" cellspacing="0" width="100%%" '
        'style="border-collapse:collapse;background-color:{bg_body};">\n'
        '<tr><td align="center" style="padding:32px 16px;">\n'
        '\n'
        '<!-- Email container 600px -->\n'
        '<table border="0" cellpadding="0" cellspacing="0" width="600" '
        'style="border-collapse:collapse;max-width:600px;width:100%%;">\n'
        '\n'
        '<!-- ====== HEADER: Logo only, minimal ====== -->\n'
        '<tr><td style="padding:0 0 24px 0;">\n'
        '<img src="{logo_url}" alt="{company}" width="120" '
        'style="display:block;height:auto;max-width:120px;'
        'border:0;outline:none;" />\n'
        '</td></tr>\n'
        '\n'
        '<!-- ====== ACCENT LINE ====== -->\n'
        '<tr><td style="padding:0;">\n'
        '<div style="height:3px;background:{navy};border-radius:2px;"></div>\n'
        '</td></tr>\n'
        '\n'
        '<!-- ====== MAIN CONTENT AREA ====== -->\n'
        '<tr><td style="background-color:{white};padding:36px 0 32px 0;">\n'
        '\n'
        '<!-- Subject as headline -->\n'
        '<table border="0" cellpadding="0" cellspacing="0" width="100%%" '
        'style="border-collapse:collapse;">\n'
        '<tr><td style="font-family:{font_head};'
        'font-size:24px;font-weight:600;color:{navy};'
        'line-height:1.35;padding-bottom:24px;">\n'
        '{subject}\n'
        '</td></tr>\n'
        '</table>\n'
        '\n'
        '<!-- Article body -->\n'
        '<table border="0" cellpadding="0" cellspacing="0" width="100%%" '
        'style="border-collapse:collapse;">\n'
        '<tr><td style="font-family:{font_body};'
        'font-size:15px;color:{text_body};line-height:1.75;">\n'
        '{body_html}\n'
        '</td></tr>\n'
        '</table>\n'
        '\n'
        '</td></tr>\n'
        '\n'
        '<!-- ====== FOOTER ====== -->\n'
        '<tr><td style="padding:28px 0 0 0;border-top:1px solid {border};">\n'
        '<table border="0" cellpadding="0" cellspacing="0" width="100%%" '
        'style="border-collapse:collapse;">\n'
        '<tr><td style="font-family:{font_body};'
        'font-size:12px;color:{text_muted};line-height:1.6;">\n'
        '<span style="color:{navy};font-weight:600;">{company}</span>'
        ' &middot; '
        '<span style="color:{text_secondary};">{tagline}</span>'
        '<br/>\n'
        '<a href="{unsub_url}" style="color:{text_muted};font-size:11px;'
        'text-decoration:underline;">Unsubscribe</a>\n'
        '</td></tr>\n'
        '</table>\n'
        '</td></tr>\n'
        '\n'
        '</table>\n'
        '<!-- /Email container -->\n'
        '\n'
        '</td></tr>\n'
        '</table>\n'
        '<!-- /Outer wrapper -->\n'
        '\n'
        '</body>\n'
        '</html>'
    ).format(
        subject=safe_subject,
        body_html=body_html,
        logo_url=safe_logo,
        company=safe_company,
        tagline=safe_tagline,
        unsub_url=safe_unsub,
        font_head=font_head,
        font_body=font_body,
        navy=NAVY,
        navy_light=NAVY_LIGHT,
        blue_accent=BLUE_ACCENT,
        white=WHITE,
        bg_body=BG_BODY,
        text_body=TEXT_BODY,
        text_secondary=TEXT_SECONDARY,
        text_muted=TEXT_MUTED,
        border=BORDER,
    )


# ── Render pipeline ──────────────────────────────────────────────────────

def _plain_text_to_html(text: str) -> str:
    """Convert plain text body to HTML paragraphs."""
    safe = html.escape(text)
    # Double newline = paragraph break, single newline = <br>
    paragraphs = safe.split("\n\n")
    html_parts = []
    for p in paragraphs:
        p = p.strip()
        if p:
            p = p.replace("\n", "<br/>")
            html_parts.append(
                '<p style="margin:0 0 16px 0;">{}</p>'.format(p)
            )
    return "".join(html_parts)


def render_newsletter(
    db: Session,
    newsletter_id: str,
    recipient_email: Optional[str] = None,
    recipient_name: Optional[str] = None,
) -> str:
    """Render full newsletter HTML with the Field Notes template.

    Args:
        db: SQLAlchemy session.
        newsletter_id: ID of the Newsletter row.
        recipient_email: Optional recipient email for personalized unsubscribe.
        recipient_name: Optional recipient name for personalization.

    Returns:
        Complete HTML string.
    """
    nl = db.query(Newsletter).filter(Newsletter.id == newsletter_id).first()
    if nl is None:
        raise ValueError("Newsletter not found: {}".format(newsletter_id))

    # Extract body text from content_json
    cj = nl.content_json or {}
    if isinstance(cj, dict):
        body_text = cj.get("body_text", "")
    elif isinstance(cj, list):
        # Legacy block format — concatenate body fields
        parts = []
        for block in cj:
            if isinstance(block, dict):
                for key in ("body", "headline", "highlight"):
                    val = block.get(key, "")
                    if val:
                        parts.append(val)
        body_text = "\n\n".join(parts)
    else:
        body_text = ""

    # Personalize
    name = recipient_name or ""
    if recipient_email:
        unsub_url = build_unsubscribe_url(recipient_email)
    else:
        unsub_url = build_unsubscribe_url("preview@example.com")

    # Replace placeholders
    body_text = body_text.replace("{{name}}", name)
    body_text = body_text.replace(
        "{{company}}",
        "Preview Company" if not recipient_email or recipient_email == "preview@example.com" else "",
    )

    # Convert to HTML
    body_html = _plain_text_to_html(body_text)

    return _field_notes_template(
        subject=nl.subject_line or nl.title or "",
        body_html=body_html,
        unsubscribe_url=unsub_url,
        recipient_name=name,
    )


def preview_newsletter(db: Session, newsletter_id: str) -> str:
    """Render preview with placeholder data."""
    return render_newsletter(
        db,
        newsletter_id,
        recipient_email="preview@example.com",
        recipient_name="Preview Recipient",
    )
