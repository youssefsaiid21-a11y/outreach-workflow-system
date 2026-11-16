"""
HubSpot CRM API v3 client — read-only interface to HubSpot's deal pipeline.

Part of Example's outreach system. This client serves two main consumers:

  1. **Follow-ups poller** (followups_poller.py): Syncs deals from configured
     pipeline stages, detects overdue ones (no activity in N days), and triggers
     Trello card creation for sales team follow-up.

  2. **Newsletter HubSpot sync** (newsletter_sender.py / newsletters.py): Pulls
     contacts from the "To Nurture" deal stage to auto-populate newsletter
     recipient lists.

Auth: Uses a HubSpot private app Bearer token (HUBSPOT_API_TOKEN env var).
The token needs scopes for deals, contacts, companies, owners, and notes.

Rate limiting: HubSpot enforces 100 requests per 10 seconds for private apps.
This client handles 429 responses with exponential backoff + jitter. Server
errors (5xx) are also retried; client errors (4xx except 429) are permanent.

Key methods:
  - list_deals(stage_ids) — paginated deal fetch with optional stage filter
  - list_nurture_contacts() — resolves contacts from "To Nurture" deals
  - get_deal_notes(deal_id) — fetches recent notes for AI next-step suggestions
  - list_pipelines() — returns all pipelines with their stages (for config UI)
  - Enrichment helpers: get_contact(), get_company(), get_owner(), list_owners()
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

HUBSPOT_BASE = "https://api.hubapi.com"

# Properties requested when fetching deals from HubSpot.
# These are the CRM field names that map to our FollowupDeal model.
DEAL_PROPERTIES = [
    "dealname", "amount", "dealstage", "hs_lastactivitydate",
    "hubspot_owner_id", "pipeline", "closedate",
    "notes_last_updated", "hs_lastmodifieddate", "createdate",
]


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter, capped at 30s. Used for retry delays."""
    return min((2 ** attempt) + random.uniform(0, 1), 30)


class HubSpotClient:
    """HubSpot CRM API client. Create an instance, use it for a batch of
    operations, then call close() to release the HTTP connection pool.

    Requires HUBSPOT_API_TOKEN env var (private app token).
    Raises ValueError on init if the token is missing.
    """

    def __init__(self, api_token: Optional[str] = None):
        self.api_token = api_token or os.getenv("HUBSPOT_API_TOKEN")
        if not self.api_token:
            raise ValueError("HUBSPOT_API_TOKEN is not set.")
        self.http = httpx.Client(timeout=30)

    def close(self):
        self.http.close()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_token}"}

    def _get(self, url: str, params: Optional[dict] = None, retries: int = 3) -> dict:
        for attempt in range(retries):
            try:
                resp = self.http.get(url, headers=self._headers(), params=params)
                if resp.status_code == 429:
                    wait = _backoff(attempt)
                    logger.warning("HubSpot rate limited, waiting %.1fs", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                # Only retry on 5xx server errors; 4xx (403, 404) are permanent
                if exc.response.status_code >= 500 and attempt < retries - 1:
                    time.sleep(_backoff(attempt))
                    continue
                raise
            except httpx.RequestError as exc:
                if attempt < retries - 1:
                    time.sleep(_backoff(attempt))
                    continue
                raise RuntimeError(f"HubSpot request failed: {exc}") from exc
        return {}

    # ── Pipelines & Stages ───────────────────────────────────────────────

    def list_pipelines(self) -> list[dict]:
        """Return all deal pipelines with their stages."""
        data = self._get(f"{HUBSPOT_BASE}/crm/v3/pipelines/deals")
        results = []
        for p in data.get("results", []):
            stages = []
            for s in p.get("stages", []):
                stages.append({
                    "id": s["id"],
                    "label": s.get("label", s["id"]),
                    "display_order": s.get("displayOrder", 0),
                })
            stages.sort(key=lambda s: s["display_order"])
            results.append({
                "id": p["id"],
                "label": p.get("label", p["id"]),
                "stages": stages,
            })
        return results

    # ── Deals ────────────────────────────────────────────────────────────

    def list_deals(self, stage_ids: Optional[list[str]] = None) -> list[dict]:
        """Fetch all deals (paginated). Optionally filter to specific stage IDs.

        Returns simplified dicts with properties + association IDs.
        """
        all_deals = []
        after = None

        while True:
            params = {
                "limit": 100,
                "properties": ",".join(DEAL_PROPERTIES),
                "associations": "contacts,companies",
            }
            if after:
                params["after"] = after

            data = self._get(f"{HUBSPOT_BASE}/crm/v3/objects/deals", params=params)

            for raw in data.get("results", []):
                props = raw.get("properties", {})
                deal_stage_id = props.get("dealstage", "")

                # Filter by stage if requested
                if stage_ids and deal_stage_id not in stage_ids:
                    continue

                # Extract association IDs
                assoc = raw.get("associations", {})
                contact_ids = [r["id"] for r in (assoc.get("contacts", {}).get("results", []))]
                company_ids = [r["id"] for r in (assoc.get("companies", {}).get("results", []))]

                amount_raw = props.get("amount")
                amount = None
                if amount_raw:
                    try:
                        amount = float(amount_raw)
                    except (ValueError, TypeError):
                        pass

                # Fallback chain for last activity:
                #   1. hs_lastactivitydate — real logged activity (calls, emails, meetings)
                #   2. notes_last_updated — someone added a note (real human action)
                #   3. createdate — when the deal was created (last resort)
                # NOTE: hs_lastmodifieddate is deliberately excluded — it updates on
                # ANY property change (including HubSpot automations), making deals
                # look "just contacted" when nobody actually followed up.
                last_activity = (
                    props.get("hs_lastactivitydate")
                    or props.get("notes_last_updated")
                    or props.get("createdate")
                )

                all_deals.append({
                    "id": raw["id"],
                    "deal_name": props.get("dealname", ""),
                    "deal_stage_id": deal_stage_id,
                    "pipeline_id": props.get("pipeline", ""),
                    "amount": amount,
                    "hubspot_owner_id": props.get("hubspot_owner_id"),
                    "last_activity_at": last_activity,
                    "close_date": props.get("closedate"),
                    "contact_ids": contact_ids,
                    "company_ids": company_ids,
                })

            paging = data.get("paging", {})
            next_page = paging.get("next", {})
            after = next_page.get("after")
            if not after:
                break

        logger.info("HubSpot: fetched %d deals", len(all_deals))
        return all_deals

    # ── Enrichment ───────────────────────────────────────────────────────

    def list_owners(self) -> dict[str, dict]:
        """Fetch all owners and return a dict keyed by owner ID.

        Falls back to empty dict if the scope isn't granted.
        """
        try:
            all_owners = {}
            after = None
            while True:
                params = {"limit": 100}
                if after:
                    params["after"] = after
                data = self._get(f"{HUBSPOT_BASE}/crm/v3/owners", params=params)
                for o in data.get("results", []):
                    all_owners[str(o.get("id", ""))] = {
                        "id": str(o.get("id", "")),
                        "email": o.get("email", ""),
                        "first_name": o.get("firstName", ""),
                        "last_name": o.get("lastName", ""),
                        "name": " ".join(x for x in [o.get("firstName"), o.get("lastName")] if x).strip(),
                    }
                paging = data.get("paging", {})
                after = paging.get("next", {}).get("after")
                if not after:
                    break
            logger.info("HubSpot: loaded %d owners", len(all_owners))
            return all_owners
        except Exception as exc:
            logger.warning("HubSpot: failed to list owners (scope missing?): %s", exc)
            return {}

    def get_owner(self, owner_id: str) -> dict:
        """Get owner details by ID."""
        try:
            data = self._get(f"{HUBSPOT_BASE}/crm/v3/owners/{owner_id}")
            return {
                "id": data.get("id", owner_id),
                "email": data.get("email", ""),
                "first_name": data.get("firstName", ""),
                "last_name": data.get("lastName", ""),
                "name": " ".join(x for x in [data.get("firstName"), data.get("lastName")] if x).strip(),
            }
        except Exception as exc:
            logger.warning("HubSpot: failed to get owner %s: %s", owner_id, exc)
            return {"id": owner_id, "email": "", "first_name": "", "last_name": "", "name": ""}

    def get_contact(self, contact_id: str) -> dict:
        """Get contact name and email."""
        try:
            data = self._get(
                f"{HUBSPOT_BASE}/crm/v3/objects/contacts/{contact_id}",
                params={"properties": "email,firstname,lastname"},
            )
            props = data.get("properties", {})
            return {
                "id": contact_id,
                "email": props.get("email") or "",
                "name": " ".join(x for x in [props.get("firstname"), props.get("lastname")] if x).strip(),
            }
        except Exception as exc:
            logger.warning("HubSpot: failed to get contact %s: %s", contact_id, exc)
            return {"id": contact_id, "email": "", "name": ""}

    def get_company(self, company_id: str) -> dict:
        """Get company name."""
        try:
            data = self._get(
                f"{HUBSPOT_BASE}/crm/v3/objects/companies/{company_id}",
                params={"properties": "name,domain"},
            )
            props = data.get("properties", {})
            return {
                "id": company_id,
                "name": props.get("name", ""),
                "domain": props.get("domain", ""),
            }
        except Exception as exc:
            logger.warning("HubSpot: failed to get company %s: %s", company_id, exc)
            return {"id": company_id, "name": "", "domain": ""}

    # ── Newsletter nurture contacts ────────────────────────────────────

    # HubSpot stage ID for the "To Nurture" deal stage.
    # Override via HUBSPOT_NURTURE_STAGE_ID env var if the pipeline is reconfigured.
    NURTURE_STAGE_ID = os.getenv("HUBSPOT_NURTURE_STAGE_ID", "1219886089")

    def list_nurture_contacts(self, stage_id: Optional[str] = None) -> list[dict]:
        """Fetch all contacts associated with deals in the 'To Nurture' stage.

        Returns deduplicated list of dicts:
            {email, name, company_name, deal_name, hubspot_contact_id}
        """
        stage = stage_id or self.NURTURE_STAGE_ID
        deals = self.list_deals(stage_ids=[stage])
        logger.info("HubSpot nurture: %d deals in stage %s", len(deals), stage)

        seen_emails: dict[str, dict] = {}  # email -> contact dict
        for deal in deals:
            # Resolve company name from first associated company
            company_name = ""
            for cid in deal.get("company_ids", [])[:1]:
                co = self.get_company(cid)
                company_name = co.get("name", "")

            for contact_id in deal.get("contact_ids", []):
                contact = self.get_contact(contact_id)
                email = (contact.get("email") or "").strip().lower()
                if not email:
                    continue
                if email in seen_emails:
                    continue  # deduplicate
                seen_emails[email] = {
                    "email": email,
                    "name": contact.get("name", ""),
                    "company_name": company_name or deal.get("deal_name", ""),
                    "deal_name": deal.get("deal_name", ""),
                    "hubspot_contact_id": contact_id,
                }

        contacts = list(seen_emails.values())
        logger.info("HubSpot nurture: %d unique contacts resolved", len(contacts))
        return contacts

    def get_deal_notes(self, deal_id: str, limit: int = 5) -> list[str]:
        """Fetch the most recent notes associated with a deal. Returns plain text."""
        try:
            # Get note IDs associated with this deal
            data = self._get(
                f"{HUBSPOT_BASE}/crm/v3/objects/deals/{deal_id}/associations/notes",
            )
            note_ids = [r["id"] for r in data.get("results", [])]
            if not note_ids:
                return []

            # Fetch note bodies (most recent first, limited)
            import re
            notes = []
            for nid in note_ids[:limit]:
                ndata = self._get(
                    f"{HUBSPOT_BASE}/crm/v3/objects/notes/{nid}",
                    params={"properties": "hs_note_body,hs_timestamp"},
                )
                body_html = ndata.get("properties", {}).get("hs_note_body", "")
                if body_html:
                    # Strip HTML tags
                    plain = re.sub(r"<[^>]+>", " ", body_html)
                    plain = re.sub(r"\s+", " ", plain).strip()
                    if plain:
                        notes.append(plain[:500])
            return notes
        except Exception as exc:
            logger.warning("HubSpot: failed to get notes for deal %s: %s", deal_id, exc)
            return []
