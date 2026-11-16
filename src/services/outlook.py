"""
Microsoft Graph API client for Outlook — email sending, draft creation, and calendar events.

This is the central email integration layer for Example's outreach system. It wraps
Microsoft Graph API v1.0 endpoints for:

  1. **Email drafts**: Conference outreach and newsletter push-to-drafts create
     personalized email drafts in the user's Outlook Drafts folder via Graph.
  2. **Email sending**: Newsletter sends use POST /me/sendMail directly.
  3. **Inbox reading**: The conference reply poller reads inbox messages to detect
     replies. The style analyzer reads sent emails to build voice profiles.
  4. **Calendar events**: Conference follow-up reminders are scheduled as Outlook
     calendar events so reps get native popup reminders.
  5. **Reply threading**: Follow-up emails (email 2, 3, ...) are created as reply
     drafts to the original email 1 so they appear in the same thread.
  6. **Mailbox view**: The dashboard mailbox tab uses this to display inbox/sent
     messages and allow inline replies.

Auth: Uses MSAL device code flow (works headless and in browser). Each user gets
their own token cache file at tokens/outlook_{user_id}.json. Token refresh is
automatic via MSAL's silent acquisition.

Scopes are split: MAIL_SCOPES (Mail.Read, Mail.ReadWrite, Mail.Send) are always
available. CALENDAR_SCOPES (Calendars.ReadWrite) are optional — existing users who
connected before calendar support was added can still draft emails without
re-consenting. Calendar operations gracefully degrade when the scope is missing.

All HTTP methods (_get, _post, _patch, _delete) include:
  - Exponential backoff with jitter for 429/5xx responses
  - Retry on transient network errors (timeout, DNS, connection reset)
  - Clear error messages for 401 (token expired — reconnect via dashboard)
"""

import os
import re as _re
import json
import random
import webbrowser
import logging
from pathlib import Path
from typing import Optional
import msal
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

try:
    from ..config import HTTP_RETRY_BACKOFF_CEILING
except ImportError:
    HTTP_RETRY_BACKOFF_CEILING = 30

# SWAT-C6: email address validation to prevent header injection
_SAFE_EMAIL_RE = _re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _validate_email_addr(addr: str) -> str:
    """Validate and return a safe email address. Raises ValueError on injection."""
    addr = addr.strip()
    if "\r" in addr or "\n" in addr:
        raise ValueError(f"Email address contains line breaks: {addr!r}")
    if "<" in addr or ">" in addr:
        raise ValueError(f"Email address contains angle brackets: {addr!r}")
    if not _SAFE_EMAIL_RE.match(addr):
        raise ValueError(f"Invalid email address format: {addr!r}")
    return addr


def _outlook_backoff(attempt: int) -> float:
    """P2-1: exponential backoff with jitter for Outlook/Graph retries."""
    return min((2 ** attempt) + random.uniform(0, 1), HTTP_RETRY_BACKOFF_CEILING)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_OUTREACH_DIR = Path(__file__).resolve().parent.parent.parent   # outreach-system/
TOKEN_DIR = _OUTREACH_DIR / "tokens"
# Legacy single-tenant cache path  -  kept as a constant only so the startup
# migration shim in main.py can move it to the new per-user location. Do NOT
# instantiate OutlookClient without a user_id in new code.
LEGACY_TOKEN_CACHE = _OUTREACH_DIR / "token_cache.json"
# Events-reminders 2026-04-09: calendar scope is OPTIONAL.
#
# Why it's split from MAIL_SCOPES:
# Originally we lumped "Calendars.ReadWrite" into one SCOPES constant and
# passed it to acquire_token_silent. That silently broke drafting for
# every existing user  -  MSAL cannot upgrade a cached token to cover a
# new scope without re-consent, so acquire_token_silent returned None
# and every draft flow raised "Outlook token expired" even though the
# mail scope was still perfectly valid.
#
# The fix: mail ops stay on MAIL_SCOPES (which existing cached tokens
# cover), calendar ops request CALENDAR_SCOPES separately via a best-
# effort acquire_token_silent that returns None instead of crashing
# when the user hasn't yet re-consented. New device-flow connections
# still request the full SCOPES set so fresh users get calendar access
# on day one; existing users can keep drafting and get follow-up
# reminders only after they reconnect.
MAIL_SCOPES = ["Mail.Read", "Mail.ReadWrite", "Mail.Send"]
CALENDAR_SCOPES = ["Calendars.ReadWrite"]
SCOPES = MAIL_SCOPES + CALENDAR_SCOPES

# These are the mail folders we read from
SENT_ITEMS_FOLDER = "SentItems"


class OutlookClient:
    """Microsoft Graph Outlook client.

    P3-3: Each instance is bound to a specific `user_id` so its token cache
    file cannot leak between reps. The cache path is
    ``outreach-system/tokens/outlook_{user_id}.json`` unless the caller
    passes an explicit ``token_file`` override (used by tests and by the
    legacy conference/style code paths that still compute their own path).
    """

    def __init__(
        self,
        user_id: Optional[str] = None,
        token_file: Optional[Path] = None,
    ):
        self.client_id = os.getenv("MICROSOFT_CLIENT_ID")
        self.tenant_id = os.getenv("MICROSOFT_TENANT_ID", "common")

        if token_file is None:
            if not user_id:
                raise ValueError(
                    "OutlookClient requires a user_id (or an explicit token_file override)"
                )
            token_file = TOKEN_DIR / f"outlook_{user_id}.json"

        self.token_file = Path(token_file)
        # Ensure the parent directory exists so the first write() call does
        # not blow up on a fresh install.
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Could not create Outlook token directory %s: %s", self.token_file.parent, e)

        if not self.client_id:
            raise ValueError("MICROSOFT_CLIENT_ID is not set.")

        # Use a serializable cache so tokens persist across restarts
        self.cache = msal.SerializableTokenCache()
        if self.token_file.exists():
            self.cache.deserialize(self.token_file.read_text())

        self.app = msal.PublicClientApplication(
            client_id=self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=self.cache,
        )
        self._access_token: Optional[str] = None
        self.http = httpx.Client(timeout=30)

    def _persist_cache(self):
        """Write the MSAL token cache to disk atomically (temp file + rename).

        This prevents partial writes when multiple OutlookClient instances
        (e.g., draft creator + reply poller) write to the same cache file
        simultaneously. os.replace is atomic on both POSIX and NTFS.
        """
        if self.cache.has_state_changed:
            try:
                # Audit 2026-05-05 #5: atomic write via temp file + rename.
                # Prevents partial writes when multiple OutlookClient
                # instances (draft + reply poller) write simultaneously.
                import tempfile
                data = self.cache.serialize()
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(self.token_file.parent),
                    suffix=".tmp",
                )
                fd_closed = False
                try:
                    os.write(fd, data.encode("utf-8"))
                    os.close(fd)
                    fd_closed = True
                    # os.replace is atomic on both POSIX and Windows (NTFS).
                    os.replace(tmp_path, str(self.token_file))
                    # Security: restrict token file to owner-only read/write
                    try:
                        os.chmod(str(self.token_file), 0o600)
                    except OSError:
                        pass  # Windows doesn't support Unix permissions
                except Exception:
                    if not fd_closed:
                        os.close(fd)
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    raise
            except OSError as e:
                logger.error("Failed to persist Outlook token cache to %s: %s", self.token_file, e)

    def authenticate(self) -> str:
        # Try loading cached token first. IMPORTANT: silently refresh with
        # MAIL_SCOPES only, not SCOPES. Cached tokens from before the
        # calendar scope was added cover only Mail.*, and asking MSAL
        # for a superset scope returns None instead of the still-valid
        # mail token  -  that breaks every draft flow for existing users.
        # The calendar methods call acquire_token_silent(CALENDAR_SCOPES)
        # separately and degrade gracefully when that returns None.
        if self.token_file.exists():
            accounts = self.app.get_accounts()
            if accounts:
                result = self.app.acquire_token_silent(MAIL_SCOPES, account=accounts[0])
                self._persist_cache()
                if result and "access_token" in result:
                    self._access_token = result["access_token"]
                    return self._access_token

        # Fall back to device code flow (works headlessly and in browser)
        flow = self.app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Failed to start device flow: {flow}")

        print(f"\n{flow['message']}\n")
        logger.info(f"Outlook auth URL: {flow['verification_uri']}  Code: {flow['user_code']}")
        try:
            webbrowser.open(flow["verification_uri"])
        except Exception:
            # Headless environment  -  browser open silently fails, which is fine.
            # The URL and code are logged above and printed to stdout for CLI use.
            pass

        result = self.app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            # SWAT-M16: log only error_description, not full result dict
            raise RuntimeError(f"Authentication failed: {result.get('error_description', 'unknown error')}")

        self._persist_cache()
        self._access_token = result["access_token"]
        logger.info("Outlook authenticated successfully.")
        return self._access_token

    def _get_token(self) -> str:
        # Mail-scope token refresh  -  see authenticate() for why we do NOT
        # ask for SCOPES (which includes the optional calendar scope).
        if self._access_token:
            return self._access_token
        if not self.token_file.exists():
            raise RuntimeError("Outlook not authenticated. Connect via the dashboard first.")
        accounts = self.app.get_accounts()
        if not accounts:
            raise RuntimeError("Outlook not authenticated. Connect via the dashboard first.")
        result = self.app.acquire_token_silent(MAIL_SCOPES, account=accounts[0])
        self._persist_cache()
        if result and "access_token" in result:
            self._access_token = result["access_token"]
            return self._access_token
        raise RuntimeError("Outlook token expired. Please reconnect via the dashboard.")

    def _get_calendar_token(self) -> Optional[str]:
        """Return a calendar-scoped access token, or None if the user
        hasn't yet consented to Calendars.ReadWrite. Best-effort: never
        raises  -  callers use None to decide whether to skip calendar
        operations entirely (so a missing scope does not block drafts).
        """
        if not self.token_file.exists():
            return None
        accounts = self.app.get_accounts()
        if not accounts:
            return None
        try:
            result = self.app.acquire_token_silent(CALENDAR_SCOPES, account=accounts[0])
        except Exception as exc:
            logger.debug("Calendar scope silent acquire failed: %s", exc)
            return None
        self._persist_cache()
        if result and "access_token" in result:
            return result["access_token"]
        return None

    def has_calendar_scope(self) -> bool:
        """True if this client's cached token can be silently refreshed
        with Calendars.ReadWrite. Used by schedule_followup_reminders to
        decide whether to bother calling Graph /me/events."""
        return self._get_calendar_token() is not None

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def _get(self, url: str, params: Optional[dict] = None) -> dict:
        import time as _time
        retries = 3
        for attempt in range(retries):
            try:
                resp = self.http.get(url, headers=self._headers(), params=params)
            except httpx.TransportError as err:
                # P2-1: retry transient network errors (timeout/connect/read/etc.)
                if attempt == retries - 1:
                    raise
                sleep_time = _outlook_backoff(attempt)
                logger.warning(
                    "Outlook GET %s: %s  -  retry %d/%d in %.1fs",
                    url, err, attempt + 1, retries, sleep_time,
                )
                _time.sleep(sleep_time)
                continue
            if resp.status_code == 401:
                raise RuntimeError("Outlook token expired or revoked. Please reconnect via the dashboard.")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < retries - 1:
                    sleep_time = _outlook_backoff(attempt)
                    logger.warning(
                        "Outlook GET %s: status=%d  -  retry %d/%d in %.1fs",
                        url, resp.status_code, attempt + 1, retries, sleep_time,
                    )
                    _time.sleep(sleep_time)
                    continue
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                raise RuntimeError(f"Graph API returned non-JSON ({resp.status_code}): {resp.text[:200]}")
        raise RuntimeError(f"Graph API request failed after {retries} retries.")

    def _post(self, url: str, payload: dict) -> dict:
        import time as _time
        retries = 3
        for attempt in range(retries):
            try:
                resp = self.http.post(url, headers=self._headers(), json=payload)
            except httpx.TransportError as err:
                # P2-1: retry transient network errors (timeout/connect/read/etc.)
                if attempt == retries - 1:
                    raise
                sleep_time = _outlook_backoff(attempt)
                logger.warning(
                    "Outlook POST %s: %s  -  retry %d/%d in %.1fs",
                    url, err, attempt + 1, retries, sleep_time,
                )
                _time.sleep(sleep_time)
                continue
            if resp.status_code == 401:
                raise RuntimeError("Outlook token expired or revoked. Please reconnect via the dashboard.")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < retries - 1:
                    sleep_time = _outlook_backoff(attempt)
                    logger.warning(
                        "Outlook POST %s: status=%d  -  retry %d/%d in %.1fs",
                        url, resp.status_code, attempt + 1, retries, sleep_time,
                    )
                    _time.sleep(sleep_time)
                    continue
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                raise RuntimeError(f"Graph API returned non-JSON ({resp.status_code}): {resp.text[:200]}")
        raise RuntimeError(f"Graph API request failed after {retries} retries.")

    def _patch(self, url: str, payload: dict) -> dict:
        """PATCH request with the same retry/backoff contract as _get/_post.

        Used by the mailbox endpoints to flip ``isRead`` on a Graph message
        and as the middle step of the createReply → PATCH body → send
        reply flow. Returns the parsed JSON body; returns ``{}`` on 204.
        """
        import time as _time
        retries = 3
        for attempt in range(retries):
            try:
                resp = self.http.patch(url, headers=self._headers(), json=payload)
            except httpx.TransportError as err:
                if attempt == retries - 1:
                    raise
                sleep_time = _outlook_backoff(attempt)
                logger.warning(
                    "Outlook PATCH %s: %s  -  retry %d/%d in %.1fs",
                    url, err, attempt + 1, retries, sleep_time,
                )
                _time.sleep(sleep_time)
                continue
            if resp.status_code == 401:
                raise RuntimeError("Outlook token expired or revoked. Please reconnect via the dashboard.")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < retries - 1:
                    sleep_time = _outlook_backoff(attempt)
                    logger.warning(
                        "Outlook PATCH %s: status=%d  -  retry %d/%d in %.1fs",
                        url, resp.status_code, attempt + 1, retries, sleep_time,
                    )
                    _time.sleep(sleep_time)
                    continue
            resp.raise_for_status()
            if resp.status_code == 204 or not resp.content:
                return {}
            try:
                return resp.json()
            except Exception:
                raise RuntimeError(f"Graph API returned non-JSON ({resp.status_code}): {resp.text[:200]}")
        raise RuntimeError(f"Graph API PATCH failed after {retries} retries.")

    def _delete(self, url: str) -> bool:
        """DELETE request with the same retry/backoff contract as _get/_post.

        Returns True on success (204 No Content) and False if the resource
        was already gone (404). Raises on other errors. Used for calendar
        event cancellation when a lead replies.
        """
        import time as _time
        retries = 3
        for attempt in range(retries):
            try:
                resp = self.http.delete(url, headers=self._headers())
            except httpx.TransportError as err:
                if attempt == retries - 1:
                    raise
                sleep_time = _outlook_backoff(attempt)
                logger.warning(
                    "Outlook DELETE %s: %s  -  retry %d/%d in %.1fs",
                    url, err, attempt + 1, retries, sleep_time,
                )
                _time.sleep(sleep_time)
                continue
            if resp.status_code == 401:
                raise RuntimeError("Outlook token expired or revoked. Please reconnect via the dashboard.")
            if resp.status_code == 404:
                # Already gone  -  treat as a successful no-op so the caller
                # can unconditionally try to cancel stale event IDs.
                return False
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < retries - 1:
                    sleep_time = _outlook_backoff(attempt)
                    logger.warning(
                        "Outlook DELETE %s: status=%d  -  retry %d/%d in %.1fs",
                        url, resp.status_code, attempt + 1, retries, sleep_time,
                    )
                    _time.sleep(sleep_time)
                    continue
            resp.raise_for_status()
            return True
        raise RuntimeError(f"Graph API DELETE failed after {retries} retries.")

    def create_calendar_event(
        self,
        *,
        subject: str,
        body_html: str,
        start_dt,
        duration_minutes: int = 15,
        reminder_minutes: int = 60,
        timezone_name: str = "UTC",
    ) -> str:
        """Create a reminder event on the user's default calendar via Graph.

        Events-reminders 2026-04-09: used by the conference flow to schedule
        a nudge for each follow-up step (email 2, 3, …) after email 1 is
        drafted. Graph accepts ``start.dateTime`` as a naive ISO 8601 string
        alongside an explicit ``timeZone`` field  -  we deliberately send the
        rep's local wall-clock time plus the IANA zone name so the reminder
        fires at 09:00 *their* time, not 09:00 UTC.

        Args:
            subject: Event title shown in the calendar.
            body_html: HTML body  -  use a direct link back to the sequence
                modal so clicking the reminder jumps straight to the draft.
            start_dt: naive ``datetime`` representing wall-clock time in
                ``timezone_name``. Do NOT pass a tz-aware datetime; Graph
                does not accept an offset here.
            duration_minutes: how long the calendar block runs. Default 15
                because a nudge event is not a meeting.
            reminder_minutes: minutes-before-start for the Outlook popup.
            timezone_name: IANA zone string, e.g. ``"Asia/Dubai"``.

        Returns: the Graph event id (string). Raises ``PermissionError``
        when the user hasn't consented to ``Calendars.ReadWrite`` yet  -
        callers that must not block draft creation catch this specific
        exception to skip calendar scheduling silently.
        """
        from datetime import timedelta as _timedelta
        # Grab a calendar-scoped token directly. Do NOT route through
        # self._headers()  -  that path calls _get_token() which only
        # returns a mail-scoped token, and Graph will 403 the calendar
        # endpoint when the token lacks Calendars.ReadWrite.
        cal_token = self._get_calendar_token()
        if cal_token is None:
            raise PermissionError(
                "Calendar scope not granted. User must reconnect Outlook "
                "to enable follow-up reminders."
            )
        end_dt = start_dt + _timedelta(minutes=duration_minutes)
        payload = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "start": {
                "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": timezone_name,
            },
            "end": {
                "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": timezone_name,
            },
            "reminderMinutesBeforeStart": int(reminder_minutes),
            "isReminderOn": True,
            # Show the rep as free  -  this is a nudge, not a busy block.
            "showAs": "free",
        }
        headers = {
            "Authorization": f"Bearer {cal_token}",
            "Content-Type": "application/json",
        }
        resp = self.http.post(f"{GRAPH_BASE}/me/events", headers=headers, json=payload)
        if resp.status_code == 403:
            # Token exists but lacks the calendar permission  -  treat as
            # "not granted" so the caller skips scheduling this time.
            raise PermissionError(
                f"Graph 403 on /me/events  -  calendar scope missing: {resp.text[:200]}"
            )
        resp.raise_for_status()
        try:
            result = resp.json()
        except Exception:
            raise RuntimeError(
                f"Graph /me/events returned non-JSON ({resp.status_code}): {resp.text[:200]}"
            )
        event_id = result.get("id")
        if not event_id:
            raise RuntimeError(f"Graph did not return an event id: {result}")
        return event_id

    def delete_calendar_event(self, event_id: str) -> bool:
        """Cancel a previously created reminder event.

        Returns True if the event was deleted, False if it was already
        gone (404) or if the calendar scope was revoked. Never raises  -
        cancellation is always best-effort; a stale reminder lingering
        on someone's calendar is not worth surfacing as an error.
        """
        if not event_id:
            return False
        cal_token = self._get_calendar_token()
        if cal_token is None:
            # Scope was revoked or never granted  -  nothing to delete via
            # our auth. Caller will drop the local id on its own.
            return False
        headers = {"Authorization": f"Bearer {cal_token}"}
        try:
            resp = self.http.delete(f"{GRAPH_BASE}/me/events/{event_id}", headers=headers)
        except httpx.TransportError:
            return False
        if resp.status_code == 404:
            return False
        if resp.status_code in (401, 403):
            return False
        resp.raise_for_status()
        return True

    def create_draft(self, to_email: str, to_name: str, subject: str, body: str, cc: str = None, signature_html: str = "") -> dict:
        """Creates a draft email in the user's Drafts folder via Microsoft Graph."""
        # SWAT-C6: validate to_email against header injection
        to_email = _validate_email_addr(to_email)

        # Convert plain text to HTML for better Outlook compatibility
        if "<" not in body or "</" not in body:
            html_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html_body = html_body.replace("\n\n", "</p><p>").replace("\n", "<br>")
            html_body = f"<html><body><p>{html_body}</p></body></html>"
        else:
            html_body = body

        # Inject the user's Outlook signature (logo, title, contact info)
        # before </body>. Graph API does not auto-append signatures, so
        # we do it server-side.
        if signature_html:
            html_body = html_body.replace("</body>", f"{signature_html}</body>")

        payload = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": to_email, "name": to_name}}],
        }
        # When we inject the signature ourselves, set isRead=True so
        # Outlook's client-side auto-signature doesn't create a duplicate.
        # When no signature is configured, leave isRead unset so the
        # Outlook fallback behavior is preserved.
        if signature_html:
            payload["isRead"] = True
        if cc:
            # SWAT-C6: validate each CC address, skip invalid ones
            cc_list = []
            for addr in cc.split(","):
                addr = addr.strip()
                if not addr:
                    continue
                try:
                    cc_list.append(_validate_email_addr(addr))
                except ValueError:
                    logger.warning("Skipping invalid CC address: %s", addr)
            if cc_list:
                payload["ccRecipients"] = [{"emailAddress": {"address": addr}} for addr in cc_list]
        result = self._post(f"{GRAPH_BASE}/me/messages", payload)
        return {"message_id": result.get("id"), "web_link": result.get("webLink")}

    def add_inline_attachment(
        self, message_id: str, filename: str, content_bytes: bytes,
        content_type: str = "image/png", content_id: str = "logo",
    ) -> dict:
        """Attach an inline image to an existing draft message.

        The image is referenced in the HTML body via cid:<content_id>.
        """
        import base64
        url = f"{GRAPH_BASE}/me/messages/{message_id}/attachments"
        payload = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": filename,
            "contentType": content_type,
            "contentBytes": base64.b64encode(content_bytes).decode("ascii"),
            "contentId": content_id,
            "isInline": True,
        }
        return self._post(url, payload)

    def fetch_sent_emails(self, count: int = 200) -> list[dict]:
        """
        Fetches the most recent sent emails. Returns a list of simplified email dicts.
        """
        emails = []
        url = f"{GRAPH_BASE}/me/mailFolders/{SENT_ITEMS_FOLDER}/messages"
        params = {
            "$top": min(50, count),
            "$select": "id,subject,bodyPreview,body,toRecipients,sentDateTime",
            "$orderby": "sentDateTime desc",
        }

        while len(emails) < count:
            data = self._get(url, params)
            for msg in data.get("value", []):
                body_text = _strip_html(msg.get("body", {}).get("content", ""))
                body_text = _strip_reply_chain(body_text)
                body_text = _strip_signature(body_text)
                if len(body_text.strip()) < 20:
                    continue
                emails.append({
                    "subject": msg.get("subject", ""),
                    "body": body_text,
                    "sent_at": msg.get("sentDateTime", ""),
                })
                if len(emails) >= count:
                    break

            next_link = data.get("@odata.nextLink")
            if not next_link or len(emails) >= count:
                break
            url = next_link
            params = None  # next_link already contains params

        logger.info(f"Fetched {len(emails)} sent emails from Outlook.")
        return emails

    def fetch_inbox_messages(self, since_iso: str, top: int = 50,
                             mailbox_email: Optional[str] = None) -> list[dict]:
        """
        Fetch inbox messages received at or after ``since_iso``.

        Events-reply-cutoff 2026-04-09: used by the conference reply
        poller to detect replies to our conference outreach. Returns
        a list of simplified message dicts with the fields we actually
        care about for matching:

            {
                "id": str,                    # Graph message id
                "conversation_id": str,       # matches the original draft's conversationId
                "from_email": str,            # lowercased
                "from_name": str,
                "subject": str,
                "body_preview": str,          # Graph's built-in ~255-char preview
                "received_at": str,           # ISO 8601 as returned by Graph
                "is_draft": bool,
            }

        ``since_iso`` must be an ISO 8601 UTC string, e.g. "2026-04-09T13:00:00Z".
        Graph's $filter syntax does not accept a timezone offset  -  it must
        be a Zulu-format string. The caller is responsible for formatting.

        ``mailbox_email`` (optional): if provided, reads from a shared
        mailbox (``/users/{mailbox}/mailFolders/inbox/messages``) instead
        of ``/me/...``. Used by the inbound lead poller to read
        demo-member-011@company.example without each user needing their own token for
        that inbox. Requires the authenticated user to have FullAccess or
        delegated permissions on the shared mailbox.

        Graph returns up to 50 messages per page; we paginate via
        ``@odata.nextLink`` until we have ``top`` messages or the inbox
        is exhausted. Drafts (``isDraft=true``) are still returned by the
        endpoint but the caller should filter them out using the
        ``is_draft`` field, because we only care about inbound replies.
        """
        messages: list[dict] = []
        # Graph limits $top to 1000 but 50 per page is plenty for polling.
        page_size = min(50, top)
        if mailbox_email:
            url = f"{GRAPH_BASE}/users/{mailbox_email}/mailFolders/inbox/messages"
        else:
            url = f"{GRAPH_BASE}/me/mailFolders/inbox/messages"
        params: "dict | None" = {
            "$top": page_size,
            "$filter": f"receivedDateTime ge {since_iso}",
            "$select": "id,conversationId,from,subject,bodyPreview,receivedDateTime,isDraft",
            "$orderby": "receivedDateTime desc",
        }

        while len(messages) < top:
            data = self._get(url, params)
            for msg in data.get("value", []):
                sender = (msg.get("from") or {}).get("emailAddress") or {}
                from_email = (sender.get("address") or "").strip().lower()
                messages.append({
                    "id": msg.get("id", ""),
                    "conversation_id": msg.get("conversationId", "") or "",
                    "from_email": from_email,
                    "from_name": sender.get("name", "") or "",
                    "subject": msg.get("subject", "") or "",
                    "body_preview": msg.get("bodyPreview", "") or "",
                    "received_at": msg.get("receivedDateTime", "") or "",
                    "is_draft": bool(msg.get("isDraft", False)),
                })
                if len(messages) >= top:
                    break

            next_link = data.get("@odata.nextLink")
            if not next_link or len(messages) >= top:
                break
            url = next_link
            params = None  # next_link already has params baked in

        logger.info(
            "OutlookClient.fetch_inbox_messages: %d messages since %s",
            len(messages), since_iso,
        )
        return messages

    # ── Mailbox view (used by /api/mailbox) ────────────────────────────────

    def _body_to_html(self, body: str, wrap: bool = True) -> str:
        """Convert plain text to HTML.

        When ``wrap=True`` (default, used by ``create_draft``), returns a
        full ``<html><body>`` document.  When ``wrap=False`` (used by
        ``create_reply_draft``), returns just the inner ``<p>...</p>``
        fragment so it can be injected into the existing reply body
        without nesting ``<html>`` tags.
        """
        if "<" in body and "</" in body:
            return body
        safe = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe = safe.replace("\n\n", "</p><p>").replace("\n", "<br>")
        if wrap:
            return f"<html><body><p>{safe}</p></body></html>"
        return f"<p>{safe}</p>"

    def get_mailbox_address(self) -> str:
        """Return the email address of this Outlook mailbox (lowercased).

        Used by the mailbox router to label the Outlook inbox for the
        current user. Reads from the MSAL cached account; falls back to
        an empty string if no account is present yet.
        """
        try:
            accounts = self.app.get_accounts()
            if accounts:
                return (accounts[0].get("username") or "").strip().lower()
        except Exception as exc:  # noqa: BLE001
            logger.warning("OutlookClient.get_mailbox_address failed: %s", exc)
        return ""

    def _shape_mailbox_message(self, msg: dict, folder: str) -> dict:
        """Normalize a Graph message dict into the common MailMessage shape
        used by ``mail_providers.OutlookMailProvider``. Only the fields the
        mailbox UI actually reads are populated; full body HTML is added
        separately by ``get_message_full``."""
        sender = (msg.get("from") or {}).get("emailAddress") or {}
        to_list = [
            {
                "email": ((r.get("emailAddress") or {}).get("address") or "").strip().lower(),
                "name": (r.get("emailAddress") or {}).get("name") or "",
            }
            for r in (msg.get("toRecipients") or [])
        ]
        return {
            "id": msg.get("id", ""),
            "thread_id": msg.get("conversationId", "") or "",
            "folder": folder,
            "from_email": (sender.get("address") or "").strip().lower(),
            "from_name": sender.get("name", "") or "",
            "to": to_list,
            "subject": msg.get("subject", "") or "",
            "body_preview": msg.get("bodyPreview", "") or "",
            "received_at": msg.get("receivedDateTime") or "",
            "sent_at": msg.get("sentDateTime") or "",
            "unread": not bool(msg.get("isRead", True)),
            "has_attachments": bool(msg.get("hasAttachments", False)),
            "is_draft": bool(msg.get("isDraft", False)),
        }

    def list_folder_messages(
        self,
        folder: str,
        *,
        q: Optional[str] = None,
        unread: Optional[bool] = None,
        cursor: Optional[str] = None,
        top: int = 50,
    ) -> dict:
        """
        List one page of messages from the given folder.

        ``folder``: ``"inbox"`` or ``"sent"`` (case-insensitive). Maps to
        Graph's ``mailFolders/inbox`` and ``mailFolders/SentItems``.
        ``q``: free-text search via Graph ``$search``. NOTE: Graph forbids
        combining ``$search`` with ``$orderby``  -  when ``q`` is set the
        result is relevance-sorted, not time-sorted.
        ``unread``: inbox folder only  -  filters to unread messages.
        ``cursor``: an opaque ``@odata.nextLink`` URL returned by a prior
        call; when set, every other parameter is ignored and the next
        page is fetched directly.

        Returns ``{"items": [MailMessage], "next_cursor": str|None}``.
        """
        if cursor:
            data = self._get(cursor)
        else:
            mf = "inbox" if str(folder).lower() == "inbox" else SENT_ITEMS_FOLDER
            url = f"{GRAPH_BASE}/me/mailFolders/{mf}/messages"
            params: dict = {
                "$top": min(max(1, top), 50),
                "$select": (
                    "id,conversationId,from,toRecipients,subject,bodyPreview,"
                    "receivedDateTime,sentDateTime,isRead,hasAttachments,isDraft"
                ),
            }
            if q:
                # $search cannot be combined with $orderby per Graph docs.
                params["$search"] = f'"{q}"'
            else:
                params["$orderby"] = (
                    "receivedDateTime desc"
                    if str(folder).lower() == "inbox"
                    else "sentDateTime desc"
                )
                if unread is True and str(folder).lower() == "inbox":
                    params["$filter"] = "isRead eq false"
            data = self._get(url, params=params)

        items = [
            self._shape_mailbox_message(m, str(folder).lower())
            for m in (data.get("value") or [])
        ]
        return {"items": items, "next_cursor": data.get("@odata.nextLink")}

    def get_message_full(self, msg_id: str) -> dict:
        """Fetch a single message with its full body. Returns the common
        MailMessage shape plus ``body_html`` / ``body_text`` fields."""
        url = f"{GRAPH_BASE}/me/messages/{msg_id}"
        params = {
            "$select": (
                "id,conversationId,from,toRecipients,ccRecipients,subject,"
                "body,bodyPreview,receivedDateTime,sentDateTime,isRead,"
                "hasAttachments,isDraft"
            ),
        }
        data = self._get(url, params=params)
        folder = "sent" if data.get("sentDateTime") and not data.get("receivedDateTime") else "inbox"
        shaped = self._shape_mailbox_message(data, folder)
        body = data.get("body") or {}
        content = body.get("content") or ""
        ctype = (body.get("contentType") or "").lower()
        if ctype == "html":
            shaped["body_html"] = content
            shaped["body_text"] = _strip_html(content)
        else:
            shaped["body_text"] = content
            shaped["body_html"] = None
        shaped["cc"] = [
            {
                "email": ((r.get("emailAddress") or {}).get("address") or "").strip().lower(),
                "name": (r.get("emailAddress") or {}).get("name") or "",
            }
            for r in (data.get("ccRecipients") or [])
        ]
        return shaped

    def reply_to_message(self, msg_id: str, body: str) -> dict:
        """
        Send a real reply to a Graph message.

        Three-step dance used because the simpler ``POST
        /me/messages/{id}/reply`` with a ``comment`` string only accepts
        plain text that Graph inlines above the quoted thread; it does
        NOT support HTML or preserve formatting. Instead we:

          1. POST /me/messages/{id}/createReply  → returns a draft id
          2. PATCH /me/messages/{draft_id}       → set HTML body
          3. POST /me/messages/{draft_id}/send   → actually sends it

        Returns ``{"sent": True, "draft_id": <str>}``.
        """
        if not msg_id:
            raise ValueError("reply_to_message requires a message id")
        html_body = self._body_to_html(body or "")

        # Step 1  -  createReply
        draft = self._post(
            f"{GRAPH_BASE}/me/messages/{msg_id}/createReply",
            payload={},
        )
        draft_id = draft.get("id")
        if not draft_id:
            raise RuntimeError(f"Graph createReply did not return a draft id: {draft}")

        # Step 2  -  PATCH body (HTML)
        self._patch(
            f"{GRAPH_BASE}/me/messages/{draft_id}",
            payload={"body": {"contentType": "HTML", "content": html_body}},
        )

        # Step 3  -  send
        self._post(f"{GRAPH_BASE}/me/messages/{draft_id}/send", payload={})

        return {"sent": True, "draft_id": draft_id}

    def create_reply_draft(self, msg_id: str, body: str, *, to_email: str = "", to_name: str = "", signature_html: str = "") -> dict:
        """Create a reply draft to an existing message WITHOUT sending it.

        Uses the same createReply flow as ``reply_to_message`` but skips
        the final send step, leaving the reply in the Drafts folder.

        If ``msg_id`` returns 404 (e.g. the original draft was sent and
        deleted), falls back to searching Sent Items by ``to_email`` to
        find the sent copy and replies to that instead.

        When replying to a Sent Items message, ``createReply`` sets the
        recipient to the original sender (ourselves). We PATCH
        ``toRecipients`` to the lead's address so the reply goes to the
        right person.

        Returns ``{"message_id": "<draft_id>"}``.
        """
        if not msg_id:
            raise ValueError("create_reply_draft requires a message id")
        html_body = self._body_to_html(body or "", wrap=False)
        # Append signature between the new content and the quoted original.
        if signature_html:
            html_body = html_body + signature_html

        # Try the provided msg_id first; if 404, search Sent Items.
        source_id = msg_id
        _from_sent_items = False
        try:
            resp = self.http.get(
                f"{GRAPH_BASE}/me/messages/{msg_id}?$select=id",
                headers=self._headers(),
            )
            if resp.status_code == 404 and to_email:
                source_id = self._find_sent_message(to_email)
                _from_sent_items = True
        except Exception:
            pass

        if not source_id:
            raise ValueError("Could not find original message to reply to.")

        # Step 1: createReply
        draft = self._post(
            f"{GRAPH_BASE}/me/messages/{source_id}/createReply",
            payload={},
        )
        draft_id = draft.get("id")
        if not draft_id:
            raise RuntimeError(f"Graph createReply did not return a draft id: {draft}")

        # Step 2: Read the draft body (contains the quoted original from
        # createReply) and PREPEND our new content so the quoted thread
        # is preserved below the follow-up text.
        draft_detail = self._get(f"{GRAPH_BASE}/me/messages/{draft_id}?$select=body")
        original_reply_body = draft_detail.get("body", {}).get("content", "")

        import re as _re
        if _re.search(r'<body[^>]*>', original_reply_body, _re.IGNORECASE):
            merged_body = _re.sub(
                r'(<body[^>]*>)',
                r'\1' + html_body,
                original_reply_body,
                count=1,
                flags=_re.IGNORECASE,
            )
        else:
            merged_body = html_body + original_reply_body

        # Fix toRecipients if we replied to our own sent message
        # (createReply on a sent item sets to=ourselves).
        patch_payload = {"body": {"contentType": "HTML", "content": merged_body}}
        if signature_html:
            patch_payload["isRead"] = True
        if to_email and _from_sent_items:
            patch_payload["toRecipients"] = [
                {"emailAddress": {"address": to_email, "name": to_name or to_email}}
            ]
        self._patch(
            f"{GRAPH_BASE}/me/messages/{draft_id}",
            payload=patch_payload,
        )

        return {"message_id": draft_id}

    def _find_sent_message(self, to_email: str) -> "str | None":
        """Search Sent Items for the most recent message to ``to_email``.
        Returns the Graph message ID or None.

        Uses ``$search`` rather than ``$filter`` because Graph's OData
        filter on nested ``toRecipients/any(...)`` is unreliable on
        personal mailboxes (often returns empty even when the message
        exists).
        """
        try:
            # SWAT-M15: URL-encode email to prevent query injection
            from urllib.parse import quote
            safe_email = quote(to_email, safe="")
            resp = self.http.get(
                f"{GRAPH_BASE}/me/mailFolders/SentItems/messages"
                f"?$search=\"{safe_email}\""
                f"&$top=1&$select=id",
                headers=self._headers(),
            )
            if resp.status_code == 200:
                msgs = resp.json().get("value", [])
                if msgs:
                    return msgs[0]["id"]
        except Exception as exc:
            logger.warning("_find_sent_message(%s) failed: %s", to_email, exc)
        return None

    def mark_message_read(self, msg_id: str, *, read: bool = True) -> None:
        """Flip ``isRead`` on a single Graph message. Best-effort."""
        if not msg_id:
            return
        try:
            self._patch(
                f"{GRAPH_BASE}/me/messages/{msg_id}",
                payload={"isRead": bool(read)},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Outlook mark_message_read(%s) failed: %s", msg_id, exc)

    def close(self):
        self.http.close()


def _strip_html(html: str) -> str:
    """Minimal HTML stripping  -  removes tags and decodes common entities."""
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Email body cleanup patterns ─────────────────────────────────────────────
# Used by fetch_sent_emails() to extract only the sender's original content
# from emails that contain reply chains, signatures, disclaimers, etc.
# This cleaned text feeds into the style analyzer (style_analyzer.py) so the
# voice profile reflects the sender's writing, not quoted content.
#
# Order matters: earlier patterns take priority when finding the cutoff point.
import re as _re
_REPLY_CUTOFF_PATTERNS = [
    # Outlook "From: X <x@x.com> Sent: ..." reply header
    _re.compile(r"\bFrom:\s+.+?\bSent:\s+", _re.IGNORECASE | _re.DOTALL),
    # Gmail / general "On <date>, <name> wrote:" style
    _re.compile(r"\bOn\s+.{5,80}\bwrote:\s*$", _re.IGNORECASE | _re.MULTILINE),
    # Outlook "-----Original Message-----"
    _re.compile(r"-{3,}\s*Original Message\s*-{3,}", _re.IGNORECASE),
    # Example legal disclaimer block
    _re.compile(r"\bDisclaimer\s*&\s*Confidentiality\s*Notice\b", _re.IGNORECASE),
    # Calendar booking link injected by Outlook add-ins
    _re.compile(r"\bBook Time To Meet With Me\b", _re.IGNORECASE),
    # MailGuard footer
    _re.compile(r"\bMessage Protected By MailGuard\b", _re.IGNORECASE),
    # Generic reply separator lines
    _re.compile(r"_{5,}|={5,}", _re.MULTILINE),
]

# Signature block markers  -  strip everything from here to end of email,
# but these appear AFTER the sign-off words so sign-off extraction still works.
_SIGNATURE_PATTERNS = [
    _re.compile(r"\bFollow\s+us\s+on\s+LinkedIn\b", _re.IGNORECASE),
    _re.compile(r"\bEmail:\s+\S+@\S+\s", _re.IGNORECASE),
    _re.compile(r"\bCEO\s+and\s+Co-Founder\b", _re.IGNORECASE),
    _re.compile(r"\bExample\s+Technologies\b", _re.IGNORECASE),
]


def _strip_signature(text: str) -> str:
    """Remove email signature block while preserving the sign-off line."""
    earliest = len(text)
    for pattern in _SIGNATURE_PATTERNS:
        m = pattern.search(text)
        if m and m.start() < earliest:
            earliest = m.start()
    return text[:earliest].strip()


def _strip_reply_chain(text: str) -> str:
    """
    Remove quoted reply content and boilerplate from an email body.
    Returns only the top-of-thread text (what this sender actually wrote).
    """
    earliest = len(text)
    for pattern in _REPLY_CUTOFF_PATTERNS:
        m = pattern.search(text)
        if m and m.start() < earliest:
            earliest = m.start()
    return text[:earliest].strip()
