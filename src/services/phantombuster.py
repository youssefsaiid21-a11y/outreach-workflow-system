"""
PhantomBuster API v2 client  -  LinkedIn automation via Phantoms.

This is the lowest-level integration with PhantomBuster's REST API. It handles
HTTP communication, authentication, rate limiting, retries, and JSON parsing.
All PhantomBuster interactions in the system go through this client.

Base URL: https://api.phantombuster.com/api/v2
Auth:     X-Phantombuster-Key header (API key from PB workspace settings)
Docs:     https://hub.phantombuster.com/docs/api

HOW PHANTOMBUSTER WORKS:
  - A "Phantom" is a pre-built automation (e.g., "LinkedIn Auto Connect")
  - Each Phantom has an agent_id and can be launched via the API
  - A "launch" creates a "container" (one run) that executes asynchronously
  - Results are fetched via container_id after the run completes
  - PB manages its own LinkedIn session cookies (li_at) configured in its UI

PHANTOMS USED BY THIS SYSTEM:
  - LinkedIn Auto Connect (PHANTOMBUSTER_CONNECT_AGENT_ID): sends connection
    requests with personalized notes. Reads leads from our CSV feed or PB
    org-storage. Processes ~20 profiles/day per PB's internal pacer.
  - LinkedIn Message Sender (PHANTOMBUSTER_MESSAGE_AGENT_ID): sends DMs to
    1st-degree connections. Reads from our CSV feed with per-lead message text.
  - LinkedIn Profile Scraper (optional): extracts profile data for enrichment.

WHO CALLS THIS:
  - linkedin_worker.py: launches phantoms, polls results, checks acceptances
  - linkedin_batch.py: _process_results uses get_container_result
  - conference features: may use scrape_linkedin_profile for enrichment

RESILIENCE:
  - Exponential backoff with jitter on transient failures
  - Rate limiting: minimum delay between consecutive API calls
  - Configurable retry count and timeout via src/config.py
"""

import logging
import os
import random
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


# ── Circuit breaker state (module-level, shared across all client instances) ──
# Prevents cascading PB API failures from burning rate limits and slowing
# the worker loop. After 3 consecutive failures, all PB requests are blocked
# for 15 minutes so the external service has time to recover.
_consecutive_failures: int = 0
_circuit_open_until: Optional[datetime] = None

_CIRCUIT_BREAKER_THRESHOLD = 3      # consecutive failures before tripping
_CIRCUIT_BREAKER_COOLDOWN_MIN = 15  # minutes the circuit stays open


class CircuitOpenError(Exception):
    """Raised when the PB circuit breaker is open (too many consecutive failures)."""
    pass

PHANTOMBUSTER_BASE = "https://api.phantombuster.com/api/v2"

try:
    from ..config import (
        PHANTOMBUSTER_API_TIMEOUT,
        PHANTOMBUSTER_RATE_LIMIT_DELAY,
        PHANTOMBUSTER_API_RETRIES,
        HTTP_RETRY_BACKOFF_CEILING,
    )
except ImportError:
    PHANTOMBUSTER_API_TIMEOUT = 60
    PHANTOMBUSTER_RATE_LIMIT_DELAY = 0.25
    PHANTOMBUSTER_API_RETRIES = 3
    HTTP_RETRY_BACKOFF_CEILING = 30


def _backoff_delay(attempt: int) -> float:
    """P2-1: exponential backoff with jitter, capped by HTTP_RETRY_BACKOFF_CEILING."""
    return min((2 ** attempt) + random.uniform(0, 1), HTTP_RETRY_BACKOFF_CEILING)


class PhantomBusterClient:
    """Thin wrapper around the PhantomBuster REST API v2.

    Instantiate with an API key (or let it read PHANTOMBUSTER_API_KEY from env).
    In multi-user deployments, the linkedin_worker creates one client per user
    using their encrypted API key from the UserCredential table.

    The client is NOT thread-safe — each thread should create its own instance.
    The _last_call timestamp enforces a minimum delay between consecutive calls
    to avoid hitting PB's rate limits (PHANTOMBUSTER_RATE_LIMIT_DELAY).
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("PHANTOMBUSTER_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "PhantomBusterClient: PHANTOMBUSTER_API_KEY is not set. "
                "Set it in .env or pass api_key."
            )
        self.client = httpx.Client(timeout=PHANTOMBUSTER_API_TIMEOUT)
        self._last_call = 0.0  # Monotonic timestamp of last API call for throttling

    # ── internals ─────────────────────────────────────────────────────────

    def _throttle(self):
        elapsed = time.time() - self._last_call
        if elapsed < PHANTOMBUSTER_RATE_LIMIT_DELAY:
            time.sleep(PHANTOMBUSTER_RATE_LIMIT_DELAY - elapsed)
        self._last_call = time.time()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-Phantombuster-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None, retries: int | None = None) -> Any:
        global _consecutive_failures, _circuit_open_until
        # Circuit breaker: reject immediately if circuit is open
        if _circuit_open_until and datetime.now(timezone.utc) < _circuit_open_until:
            raise CircuitOpenError(f"PB circuit open until {_circuit_open_until.isoformat()}")
        if retries is None:
            retries = PHANTOMBUSTER_API_RETRIES
        url = f"{PHANTOMBUSTER_BASE}{path}"
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.client.get(url, headers=self._headers, params=params)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    logger.warning("PhantomBuster rate limit on GET %s, waiting %ds", path, retry_after)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                # Success: reset circuit breaker
                _consecutive_failures = 0
                _circuit_open_until = None
                try:
                    return resp.json()
                except Exception:
                    raise RuntimeError(
                        f"PhantomBuster returned non-JSON ({resp.status_code}): {resp.text[:200]}"
                    )
            except (httpx.HTTPStatusError, httpx.TransportError) as err:
                # Check for 412 specifically (PB workflow master stopped)
                if isinstance(err, httpx.HTTPStatusError) and err.response.status_code == 412:
                    logger.critical(
                        "PhantomBuster GET %s returned 412: PhantomBuster workflow master is stopped. "
                        "Re-enable the master Flow in the PB UI.",
                        path,
                    )
                # Circuit breaker: track consecutive failures
                _consecutive_failures += 1
                if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
                    _circuit_open_until = datetime.now(timezone.utc) + timedelta(minutes=_CIRCUIT_BREAKER_COOLDOWN_MIN)
                    logger.critical(
                        "PB circuit breaker OPEN: %d consecutive failures. "
                        "All PB requests blocked until %s",
                        _consecutive_failures, _circuit_open_until.isoformat(),
                    )
                if attempt == retries - 1:
                    raise
                sleep_time = _backoff_delay(attempt)
                logger.warning(
                    "PhantomBuster GET %s: %s  -  retry %d/%d in %.1fs",
                    path, err, attempt + 1, retries, sleep_time,
                )
                time.sleep(sleep_time)
        raise RuntimeError(f"PhantomBuster GET {path} failed after {retries} retries")

    def _post(self, path: str, payload: dict | None = None, retries: int | None = None) -> Any:
        global _consecutive_failures, _circuit_open_until
        # Circuit breaker: reject immediately if circuit is open
        if _circuit_open_until and datetime.now(timezone.utc) < _circuit_open_until:
            raise CircuitOpenError(f"PB circuit open until {_circuit_open_until.isoformat()}")
        if retries is None:
            retries = PHANTOMBUSTER_API_RETRIES
        url = f"{PHANTOMBUSTER_BASE}{path}"
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.client.post(url, headers=self._headers, json=payload or {})
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    logger.warning("PhantomBuster rate limit on POST %s, waiting %ds", path, retry_after)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                # Success: reset circuit breaker
                _consecutive_failures = 0
                _circuit_open_until = None
                try:
                    return resp.json()
                except Exception:
                    if resp.status_code == 204 or not resp.content:
                        return {}
                    raise RuntimeError(
                        f"PhantomBuster returned non-JSON ({resp.status_code}): {resp.text[:200]}"
                    )
            except (httpx.HTTPStatusError, httpx.TransportError) as err:
                # Capture the PhantomBuster response body on HTTP errors. The
                # body carries the actual precondition reason (e.g. why a
                # /agents/launch returns 412), which the bare httpx error
                # string omits. Without this the failure cause is invisible.
                body = ""
                if isinstance(err, httpx.HTTPStatusError):
                    try:
                        body = f" body={err.response.text[:300]!r}"
                    except Exception:
                        body = ""
                    # Check for 412 specifically (PB workflow master stopped)
                    if err.response.status_code == 412:
                        logger.critical(
                            "PhantomBuster POST %s returned 412: PhantomBuster workflow master is stopped. "
                            "Re-enable the master Flow in the PB UI.%s",
                            path, body,
                        )
                # Circuit breaker: track consecutive failures
                _consecutive_failures += 1
                if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
                    _circuit_open_until = datetime.now(timezone.utc) + timedelta(minutes=_CIRCUIT_BREAKER_COOLDOWN_MIN)
                    logger.critical(
                        "PB circuit breaker OPEN: %d consecutive failures. "
                        "All PB requests blocked until %s",
                        _consecutive_failures, _circuit_open_until.isoformat(),
                    )
                if attempt == retries - 1:
                    if body:
                        logger.warning("PhantomBuster POST %s failed:%s", path, body)
                    raise
                sleep_time = _backoff_delay(attempt)
                logger.warning(
                    "PhantomBuster POST %s: %s%s  -  retry %d/%d in %.1fs",
                    path, err, body, attempt + 1, retries, sleep_time,
                )
                time.sleep(sleep_time)
        raise RuntimeError(f"PhantomBuster POST {path} failed after {retries} retries")

    def _delete(self, path: str, payload: dict | None = None, retries: int | None = None) -> Any:
        if retries is None:
            retries = PHANTOMBUSTER_API_RETRIES
        url = f"{PHANTOMBUSTER_BASE}{path}"
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.client.request("DELETE", url, headers=self._headers, json=payload)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    logger.warning("PhantomBuster rate limit on DELETE %s, waiting %ds", path, retry_after)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                if resp.status_code == 204 or not resp.content:
                    return {}
                return resp.json()
            except (httpx.HTTPStatusError, httpx.TransportError) as err:
                if attempt == retries - 1:
                    raise
                sleep_time = _backoff_delay(attempt)
                logger.warning(
                    "PhantomBuster DELETE %s: %s  -  retry %d/%d in %.1fs",
                    path, err, attempt + 1, retries, sleep_time,
                )
                time.sleep(sleep_time)
        raise RuntimeError(f"PhantomBuster DELETE {path} failed after {retries} retries")

    # ── auth check ────────────────────────────────────────────────────────

    def check_api_key(self) -> bool:
        """Verify the API key by listing agents."""
        try:
            self._get("/agents/fetch-all")
            return True
        except Exception as e:
            logger.error("PhantomBuster API key check failed: %s", e)
            return False

    # ── agents (Phantoms) ─────────────────────────────────────────────────

    def list_agents(self) -> list[dict]:
        """List all Phantoms in the workspace."""
        data = self._get("/agents/fetch-all")
        if isinstance(data, list):
            return data
        return data.get("data", data.get("agents", [])) if isinstance(data, dict) else []

    def get_agent(self, agent_id: str) -> dict:
        """Get a single Phantom's metadata including s3Folder for results."""
        return self._get("/agents/fetch", params={"id": agent_id})

    def launch_agent(
        self,
        agent_id: str,
        bonus_argument: dict | None = None,
        save_argument: bool = False,
    ) -> dict:
        """
        Launch a Phantom asynchronously.

        Args:
            agent_id:       The Phantom's agent ID.
            bonus_argument: Dynamic inputs that override the Phantom's saved
                            config for this run only. Keys must match the
                            Phantom's argument schema (visible in UI → JSON view).
                            Example for LinkedIn Auto Connect:
                              {"spreadsheetUrl": "...", "message": "Hi #firstName#..."}
            save_argument:  If True, bonusArgument becomes the new default.

        Returns:
            Dict with containerId for tracking the run.
        """
        payload: dict[str, Any] = {"id": agent_id}
        if bonus_argument:
            payload["bonusArgument"] = bonus_argument
        if save_argument:
            payload["saveBonusArgument"] = True
        return self._post("/agents/launch", payload)

    def stop_agent(self, agent_id: str) -> dict:
        """Stop a running Phantom."""
        return self._post("/agents/stop", {"id": agent_id})

    def get_agent_output(self, agent_id: str) -> dict:
        """
        Get output from the most recent container (run).

        Returns the Phantom's console output and status. Use this to poll
        for completion and retrieve inline results.
        """
        return self._get("/agents/fetch-output", params={"id": agent_id})

    # ── containers (individual runs) ──────────────────────────────────────

    def list_containers(self, agent_id: str) -> list[dict]:
        """List all containers (runs) for a Phantom."""
        data = self._get("/containers/fetch-all", params={"agentId": agent_id})
        if isinstance(data, list):
            return data
        return data.get("containers", []) if isinstance(data, dict) else []

    def get_container(self, container_id: str) -> dict:
        """Get metadata for a specific container (run)."""
        return self._get("/containers/fetch", params={"id": container_id})

    def get_container_result(self, container_id: str) -> Any:
        """
        Get the structured result object from a completed container.

        This is the richest output  -  contains parsed data like profiles
        scraped, connections sent, messages delivered, errors, etc.
        """
        return self._get("/containers/fetch-result-object", params={"id": container_id})

    def get_container_output(self, container_id: str) -> dict:
        """Get console output/logs from a specific container."""
        return self._get("/containers/fetch-output", params={"id": container_id})

    # ── leads management (org-storage) ────────────────────────────────────

    def save_lead(self, lead: dict) -> dict:
        """Save a single lead to the leads database."""
        return self._post("/org-storage/leads/save", lead)

    def save_leads_bulk(self, leads: list[dict]) -> dict:
        """Bulk save leads (up to 1000 per call)."""
        return self._post("/org-storage/leads/save-many", {"leads": leads})

    def get_leads_by_list(self, list_id: str, offset: int = 0, limit: int = 100) -> dict:
        """Fetch leads from a specific list."""
        return self._post(
            f"/org-storage/leads/by-list/{list_id}",
            {"offset": offset, "limit": limit},
        )

    def delete_leads(self, lead_ids: list[str]) -> dict:
        """Bulk delete leads by ID."""
        return self._post("/org-storage/leads/delete-many", {"ids": lead_ids})

    def search_leads(self, query: dict) -> dict:
        """Search lead objects with filters."""
        return self._post("/org-storage/leads-objects/search", query)

    # ── lists management ──────────────────────────────────────────────────

    def list_all_lists(self) -> list[dict]:
        """Get all lead lists."""
        data = self._get("/org-storage/lists/fetch-all")
        if isinstance(data, list):
            return data
        return data.get("lists", []) if isinstance(data, dict) else []

    def create_list(self, name: str) -> dict:
        """Create a new lead list."""
        return self._post("/org-storage/lists/save", {"name": name})

    def delete_list(self, list_id: str) -> dict:
        """Delete a lead list."""
        return self._post("/org-storage/lists/delete", {"id": list_id})

    # ── helper: LinkedIn-specific workflows ───────────────────────────────

    def launch_linkedin_connect(
        self,
        agent_id: str,
        spreadsheet_url: str,
        message: str = "",
        max_profiles: int = 20,
    ) -> dict:
        """
        Launch the LinkedIn Auto Connect Phantom.

        Args:
            agent_id:        The Auto Connect Phantom's ID.
            spreadsheet_url: URL of Google Sheet/CSV with LinkedIn profile URLs.
            message:         Connection note (supports #firstName#, #company#, etc.).
            max_profiles:    Max profiles to process in this run.

        Returns:
            Launch result with containerId.
        """
        bonus = {
            "spreadsheetUrl": spreadsheet_url,
            "numberOfProfiles": max_profiles,
        }
        if message:
            bonus["message"] = message
        return self.launch_agent(agent_id, bonus_argument=bonus)

    def launch_linkedin_message(
        self,
        agent_id: str,
        spreadsheet_url: str,
        message: str,
        max_profiles: int = 20,
    ) -> dict:
        """
        Launch the LinkedIn Message Sender Phantom.

        Args:
            agent_id:        The Message Sender Phantom's ID.
            spreadsheet_url: URL of Google Sheet/CSV with LinkedIn profile URLs.
            message:         Message text (supports #firstName#, #company#, etc.).
            max_profiles:    Max profiles to process in this run.

        Returns:
            Launch result with containerId.
        """
        return self.launch_agent(agent_id, bonus_argument={
            "spreadsheetUrl": spreadsheet_url,
            "message": message,
            "numberOfProfiles": max_profiles,
        })

    def scrape_linkedin_profile(
        self,
        agent_id: str,
        linkedin_url: str,
        poll_interval: int = 10,
        poll_timeout: int = 120,
    ) -> dict | None:
        """Launch PB Profile Scraper for a single URL, poll until done, return parsed result.

        Returns a dict with {linkedin_headline, linkedin_summary, company} or
        None if the slot is busy, the scrape fails, or times out.

        The PB Starter plan allows only 1 parallel execution. If the agent is
        already running (e.g. LinkedIn Connect worker), this returns None
        immediately so the caller can skip and retry later.
        """
        import json as _json

        # 1. Shared slot guard - check if agent is idle
        try:
            output = self.get_agent_output(agent_id)
            current_status = output.get("containerStatus") or output.get("status", "")
            if current_status == "running":
                logger.info("scrape_linkedin_profile: agent %s is busy, skipping", agent_id)
                return None
        except Exception:
            pass  # Can't check - proceed anyway

        # 2. Normalise URL
        url = linkedin_url.strip()
        if url.startswith("http://"):
            url = "https://" + url[7:]

        # 3. Launch
        try:
            launch = self.launch_agent(agent_id, bonus_argument={
                "spreadsheetUrl": url,
                "numberOfProfiles": 1,
            })
        except Exception as exc:
            logger.warning("scrape_linkedin_profile: launch failed: %s", exc)
            return None

        container_id = launch.get("containerId", "")
        if not container_id:
            return None

        # 4. Poll until finished
        elapsed = 0
        c_status = ""
        while elapsed < poll_timeout:
            time.sleep(poll_interval)
            elapsed += poll_interval
            try:
                container = self.get_container(container_id)
                c_status = container.get("status", "")
                if c_status in ("finished", "error"):
                    break
            except Exception:
                pass

        if c_status != "finished":
            logger.warning(
                "scrape_linkedin_profile: container %s ended with status=%s",
                container_id, c_status,
            )
            return None

        # 5. Parse result
        try:
            result_data = self.get_container_result(container_id)
            result_obj = None
            if isinstance(result_data, dict):
                result_obj = result_data.get("resultObject")
                if isinstance(result_obj, str):
                    result_obj = _json.loads(result_obj)

            if isinstance(result_obj, list) and result_obj:
                profile = result_obj[0]
            elif isinstance(result_obj, dict):
                profile = result_obj
            else:
                # No structured result - try console output for basic data
                logger.info("scrape_linkedin_profile: no resultObject for %s", container_id)
                return None

            # Map PB fields to our schema
            headline = (
                profile.get("headline")
                or profile.get("occupation")
                or profile.get("currentJobTitle", "")
            )
            summary = (
                profile.get("summary")
                or profile.get("about")
                or profile.get("linkedinDescription", "")
            )
            company = (
                profile.get("companyName")
                or profile.get("currentCompany", "")
            )
            # Fallback: extract company from experiences
            if not company:
                exps = profile.get("experiences") or profile.get("positions") or []
                if isinstance(exps, list):
                    for exp in exps:
                        if isinstance(exp, dict) and not exp.get("endDate"):
                            company = exp.get("companyName") or exp.get("company", "")
                            if company:
                                break

            return {
                "linkedin_headline": (headline or "")[:200],
                "linkedin_summary": (summary or "")[:500],
                "company": (company or ""),
            }
        except Exception as exc:
            logger.warning("scrape_linkedin_profile: parse error: %s", exc)
            return None

    def get_run_results(self, agent_id: str) -> dict:
        """
        Get the latest run output for a Phantom.

        Convenience method that combines agent output with container result.
        Returns dict with status, output text, and result object if available.
        """
        output = self.get_agent_output(agent_id)
        container_id = output.get("containerId")
        result_object = None

        if container_id:
            try:
                result_object = self.get_container_result(container_id)
            except Exception:
                pass  # result may not be available yet

        return {
            "status": output.get("containerStatus", output.get("status")),
            "container_id": container_id,
            "output": output.get("output", ""),
            "result": result_object,
        }

    # ── cleanup ───────────────────────────────────────────────────────────

    def close(self):
        self.client.close()
