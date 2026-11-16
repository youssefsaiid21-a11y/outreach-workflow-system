"""
Email verification via self-hosted Reacher (https://reacher.email).

Drop-in replacement for the former ZeroBounce integration.  Reacher is
open-source and runs on our own VPS, so there are no per-email credits.

Resilience features:
- Escalating retry with 30s / 60s / 90s timeouts per email
- Circuit breaker: pauses batch if 5+ consecutive connection errors
- Lightweight health check (GET /) instead of validating a real email
- Per-email error handling  -  one failure never blocks the rest
"""
import os
import time
import logging
from typing import Optional, Callable

import httpx
from dotenv import load_dotenv
from ..config import VALID_EMAIL_STATUSES, INVALID_EMAIL_STATUSES, UNCERTAIN_EMAIL_STATUSES

load_dotenv()

logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # emails per chunk (self-hosted, be gentle on SMTP)

# Retry timeouts in seconds  -  escalating per attempt
RETRY_TIMEOUTS = [30, 60, 90]

# Circuit breaker: consecutive failures before pausing
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_COOLDOWN = 30  # seconds to wait before retrying after breaker trips

# Map Reacher's is_reachable verdict to our normalised status strings.
_REACHABLE_TO_STATUS = {
    "safe":    "valid",
    "risky":   "catch-all",
    "invalid": "invalid",
    "unknown": "unknown",
}


class ReacherClient:
    """Validates emails via a self-hosted Reacher instance."""

    def __init__(self, base_url: Optional[str] = None):
        from ..config import REACHER_DEFAULT_URL
        self.base_url = (
            base_url
            or os.getenv("REACHER_BASE_URL", "").rstrip("/")
            or REACHER_DEFAULT_URL
        )
        self._consecutive_failures = 0

    # ------------------------------------------------------------------
    # Health check  -  lightweight, no SMTP probe
    # ------------------------------------------------------------------

    def is_healthy(self) -> bool:
        """Quick connectivity check via GET /. No SMTP probe."""
        try:
            resp = httpx.get(f"{self.base_url}/", timeout=5)
            return resp.status_code < 500
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Credits  -  Reacher is self-hosted so credits are unlimited.
    # ------------------------------------------------------------------

    def get_credits(self) -> int:
        """Returns -1 to signal unlimited (self-hosted)."""
        return -1

    # ------------------------------------------------------------------
    # Single validation with escalating retry
    # ------------------------------------------------------------------

    def validate_single(self, email: str) -> dict:
        """
        Validate one email with escalating timeouts (30s → 60s → 90s).
        Returns a result dict on success or after all retries are exhausted.
        """
        last_error = None
        for attempt, timeout in enumerate(RETRY_TIMEOUTS):
            try:
                client = httpx.Client(timeout=timeout)
                try:
                    resp = client.post(
                        f"{self.base_url}/v0/check_email",
                        json={"to_email": email},
                        headers={"Content-Type": "application/json"},
                    )
                    resp.raise_for_status()
                    self._consecutive_failures = 0  # reset circuit breaker
                    return _normalize_result(email, resp.json())
                finally:
                    client.close()
            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    "Reacher timeout for %s (attempt %d/%d, timeout=%ds): %s",
                    email, attempt + 1, len(RETRY_TIMEOUTS), timeout, e,
                )
                continue
            except httpx.ConnectError as e:
                # Reacher is unreachable  -  count toward circuit breaker
                self._consecutive_failures += 1
                last_error = e
                logger.warning(
                    "Reacher connection error for %s (consecutive_failures=%d): %s",
                    email, self._consecutive_failures, e,
                )
                break  # don't retry connection errors, they'll all fail
            except Exception as e:
                last_error = e
                logger.warning(
                    "Reacher error for %s (attempt %d/%d): %s",
                    email, attempt + 1, len(RETRY_TIMEOUTS), e,
                )
                break  # non-timeout errors are unlikely to resolve with retry

        # All retries exhausted  -  return unknown so the lead isn't lost
        return _error_result(email, f"smtp_timeout" if isinstance(last_error, httpx.TimeoutException) else f"reacher_error: {last_error}")

    # ------------------------------------------------------------------
    # Batch validation with circuit breaker and per-email checkpointing
    # ------------------------------------------------------------------

    def validate_batch(
        self,
        emails: list[str],
        on_result: Optional[Callable[[str, dict], None]] = None,
    ) -> list[dict]:
        """
        Validate a list of emails with circuit breaker protection.

        Args:
            emails: List of email addresses to validate.
            on_result: Optional callback fired after each email is validated.
                       Signature: on_result(email, result_dict).
                       Use this for per-email DB checkpointing.
        """
        all_results: list[dict] = []
        for i, email in enumerate(emails):
            # Circuit breaker  -  Reacher is down, pause and retry
            if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                logger.warning(
                    "Circuit breaker tripped (%d consecutive failures). "
                    "Pausing %ds before retrying...",
                    self._consecutive_failures, CIRCUIT_BREAKER_COOLDOWN,
                )
                time.sleep(CIRCUIT_BREAKER_COOLDOWN)
                # Test if Reacher recovered
                if self.is_healthy():
                    logger.info("Reacher recovered. Resuming validation.")
                    self._consecutive_failures = 0
                else:
                    # Still down  -  mark remaining emails as unknown
                    logger.error(
                        "Reacher still unreachable after cooldown. "
                        "Marking remaining %d emails as unknown.",
                        len(emails) - i,
                    )
                    for remaining_email in emails[i:]:
                        result = _error_result(remaining_email, "reacher_unavailable")
                        all_results.append(result)
                        if on_result:
                            on_result(remaining_email, result)
                    break

            result = self.validate_single(email)
            all_results.append(result)

            # Fire checkpoint callback
            if on_result:
                on_result(email, result)

            # Small delay between checks to be gentle on SMTP
            if i < len(emails) - 1:
                time.sleep(0.3)

        # Log usage for auditing (provider = "reacher", cost = 0)
        if all_results:
            from ..db.credits import log_credits
            log_credits("reacher", "validate", credits=len(all_results))

        return all_results

    def close(self):
        """No persistent connection to close  -  kept for interface compat."""
        pass


def _error_result(email: str, sub_status: str) -> dict:
    """Build a result dict for a failed validation  -  lead is NOT lost."""
    return {
        "email": email,
        "status": "unknown",
        "sub_status": sub_status,
        "is_safe": False,
        "is_risky": False,
        "free_email": False,
        "mx_found": "",
        "smtp_provider": "",
    }


def _normalize_result(email: str, raw: dict) -> dict:
    """Map Reacher's JSON response to the standard result dict."""
    reachable = (raw.get("is_reachable") or "unknown").lower()
    status = _REACHABLE_TO_STATUS.get(reachable, "unknown")

    # Extract sub-status detail from Reacher's nested objects
    smtp = raw.get("smtp", {})
    mx = raw.get("mx", {})
    misc = raw.get("misc", {})
    syntax = raw.get("syntax", {})

    sub_status = ""
    if reachable == "invalid":
        if not syntax.get("is_valid_syntax", True):
            sub_status = "bad_syntax"
        elif not mx.get("accepts_mail", True):
            sub_status = "no_mx"
        else:
            sub_status = smtp.get("error", {}).get("type", "mailbox_not_found") if isinstance(smtp.get("error"), dict) else "mailbox_not_found"
    elif reachable == "risky":
        if smtp.get("is_catch_all", False):
            sub_status = "catch_all"
            status = "catch-all"
        elif misc.get("is_disposable", False):
            sub_status = "disposable"
            status = "invalid"
        elif misc.get("is_role_account", False):
            sub_status = "role_account"

    free_email = misc.get("is_disposable", False)

    return {
        "email": email,
        "status": status,
        "sub_status": sub_status,
        "is_safe": status in VALID_EMAIL_STATUSES,
        "is_risky": status in INVALID_EMAIL_STATUSES,
        "free_email": free_email,
        "mx_found": str(mx.get("accepts_mail", "")),
        "smtp_provider": "",
    }
