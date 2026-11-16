"""
Draft creation service.
Supports two backends:
  - "outlook": Microsoft Graph API (personal inbox already configured)
  - "imap": any IMAP inbox via APPEND to Drafts folder
"""
import imaplib
import email as email_lib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate

logger = logging.getLogger(__name__)


def create_draft_outlook(
    to_email: str, to_name: str, subject: str, body: str,
    token_file=None, cc: str = None, signature_html: str = "",
) -> dict:
    """Create a draft via Microsoft Graph. token_file selects which account to use."""
    from pathlib import Path
    from .outlook import OutlookClient
    tf = Path(token_file) if token_file else None
    client = OutlookClient(token_file=tf)
    result = client.create_draft(to_email, to_name, subject, body, cc=cc, signature_html=signature_html)
    client.close()
    return result


def create_reply_draft_outlook(
    original_msg_id: str, body: str, token_file=None,
    to_email: str = "", to_name: str = "", signature_html: str = "",
) -> dict:
    """Create a reply draft to an existing Outlook message (stays in Drafts).
    Falls back to searching Sent Items if the original draft was already sent."""
    from pathlib import Path
    from .outlook import OutlookClient
    tf = Path(token_file) if token_file else None
    client = OutlookClient(token_file=tf)
    result = client.create_reply_draft(
        original_msg_id, body, to_email=to_email, to_name=to_name,
        signature_html=signature_html,
    )
    client.close()
    return result


def create_draft_imap(
    to_email: str,
    to_name: str,
    subject: str,
    body: str,
    from_email: str,
    from_name: str,
    imap_host: str,
    imap_port: int,
    imap_password: str,
    use_ssl: bool = True,
) -> dict:
    """Append a draft message to the IMAP Drafts folder."""
    # Build the RFC 2822 message
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((from_name, from_email))
    msg["To"] = formataddr((to_name, to_email))
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    raw = msg.as_bytes()

    # Connect to IMAP
    if use_ssl:
        imap = imaplib.IMAP4_SSL(imap_host, imap_port or 993)
    else:
        imap = imaplib.IMAP4(imap_host, imap_port or 143)

    try:
        imap.login(from_email, imap_password)

        # Find the Drafts folder  -  common names vary by provider
        draft_folder = _find_drafts_folder(imap)
        if not draft_folder:
            raise RuntimeError("Could not find Drafts folder on IMAP server.")

        # APPEND the message as a draft (\\Draft flag, no \\Seen)
        result = imap.append(
            draft_folder,
            "\\Draft",
            imaplib.Time2Internaldate(None),
            raw,
        )
        if result[0] != "OK":
            raise RuntimeError(f"IMAP APPEND failed: {result}")

        return {"message_id": None, "folder": draft_folder}
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _find_drafts_folder(imap: imaplib.IMAP4) -> str | None:
    """Try common Drafts folder names across providers."""
    candidates = [
        "Drafts", "INBOX.Drafts", "[Gmail]/Drafts", "Draft",
        "Drafts/", "[Google Mail]/Drafts",
    ]
    _, folders = imap.list()
    folder_names = []
    for f in folders or []:
        if isinstance(f, bytes):
            f = f.decode()
        # Extract the folder name (last part after quotes or space)
        parts = f.split('"/"')
        if len(parts) > 1:
            folder_names.append(parts[-1].strip().strip('"'))
        else:
            folder_names.append(f.split()[-1].strip('"'))

    def _quote(name: str) -> str:
        """Always quote folder names for IMAP to handle spaces, brackets, and dots."""
        return f'"{name}"'

    for candidate in candidates:
        if candidate in folder_names:
            return _quote(candidate)

    # Case-insensitive fallback
    for name in folder_names:
        if "draft" in name.lower():
            return _quote(name)

    return None
