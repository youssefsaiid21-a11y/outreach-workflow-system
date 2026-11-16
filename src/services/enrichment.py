import os
import re
import time
import logging
import httpx
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

HUNTER_BASE = "https://api.hunter.io/v2"


class EnrichmentClient:
    """
    Enriches prospect data using a fallback chain:
      1. Hunter.io       -  finds LinkedIn URL and verifies email if missing
      2. PhantomBuster   -  scrapes LinkedIn profile for headline + summary
      3. Website         -  scrapes company About page for a company description

    All providers are optional. If an API key is not set the provider is
    skipped silently. Enrichment is additive  -  existing values are never
    overwritten.
    """

    def __init__(self):
        self.hunter_key = os.getenv("HUNTER_API_KEY")
        self._pb_key = os.getenv("PHANTOMBUSTER_API_KEY")
        self._pb_client = None  # lazy init
        self.client = httpx.Client(timeout=20, follow_redirects=True)
        # SWAT-H8: default to SSL verification. Fallback to verify=False is done
        # per-request in _scrape_company() when an SSL error is caught, not globally.
        self._client_no_ssl = httpx.Client(timeout=10, follow_redirects=True, verify=False)
        logger.debug("EnrichmentClient: no-SSL fallback client active for website scraping only.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich(self, prospect: dict) -> dict:
        """
        Returns a copy of prospect with any missing fields filled in.
        Fields enriched: linkedin_url, email, linkedin_headline,
                         linkedin_summary, company_description.
        """
        enriched = dict(prospect)

        # Step 1  -  Hunter: find LinkedIn URL or fill missing email
        if self.hunter_key:
            if not enriched.get("linkedin_url") or not enriched.get("email"):
                hunter_data = self._hunter_find(enriched)
                enriched = _merge(enriched, hunter_data)

        # Step 2  -  PhantomBuster Profile Scraper: LinkedIn profile details
        if self._pb_key and enriched.get("linkedin_url"):
            if not enriched.get("linkedin_headline"):
                pb_data = self._pb_scrape_person(enriched["linkedin_url"])
                enriched = _merge(enriched, pb_data)

        # Step 3  -  Website scraping: company description
        if enriched.get("company_website") and not enriched.get("company_description"):
            website_data = self._scrape_company(enriched["company_website"])
            enriched = _merge(enriched, website_data)

        return enriched

    def enrich_batch(self, prospects: list[dict]) -> list[dict]:
        """Enriches a list of prospects. Logs errors per prospect but never raises."""
        results = []
        for p in prospects:
            try:
                results.append(self.enrich(p))
            except Exception as e:
                logger.error(f"Enrichment failed for {p.get('email')}: {e}")
                results.append(p)
        return results

    def close(self):
        self.client.close()
        self._client_no_ssl.close()

    # ------------------------------------------------------------------
    # Hunter.io
    # ------------------------------------------------------------------

    def _hunter_find(self, prospect: dict) -> dict:
        """
        Calls Hunter person-finder to get email and/or LinkedIn URL.
        Docs: https://hunter.io/api-documentation/v2#email-finder
        """
        first = prospect.get("first_name", "")
        last = prospect.get("last_name", "")
        domain = _extract_domain(prospect.get("company_website") or "")

        if not domain or not first or not last:
            return {}

        try:
            resp = self.client.get(
                f"{HUNTER_BASE}/email-finder",
                params={
                    "domain": domain,
                    "first_name": first,
                    "last_name": last,
                    "api_key": self.hunter_key,
                },
            )
            if resp.status_code == 429:
                # Audit 2026-05-05 #30: signal rate limit to callers so
                # batch enrichment can back off instead of burning through
                # remaining prospects at full speed.
                logger.warning("Hunter.io rate limit hit  -  signalling backoff.")
                return {"_rate_limited": True}
            resp.raise_for_status()
            data = resp.json().get("data", {})

            result = {}
            if data.get("email") and not prospect.get("email"):
                result["email"] = data["email"]
                logger.info(f"Hunter found email for {first} {last}: {data['email']}")

            # Hunter returns linkedin_url inside sources sometimes
            for source in data.get("sources", []):
                uri = source.get("uri", "")
                if "linkedin.com/in/" in uri and not prospect.get("linkedin_url"):
                    result["linkedin_url"] = uri.split("?")[0]
                    break

            return result

        except Exception as e:
            logger.warning(f"Hunter lookup failed for {first} {last}: {e}")
            return {}

    # ------------------------------------------------------------------
    # PhantomBuster LinkedIn Profile Scraper (replaces Proxycurl)
    # ------------------------------------------------------------------

    def _pb_scrape_person(self, linkedin_url: str) -> dict:
        """Scrape a LinkedIn profile via PB Profile Scraper.

        Returns {linkedin_headline, linkedin_summary, company} or {} on failure.
        Uses the shared PB execution slot - returns {} immediately if the slot
        is busy (LinkedIn Connect worker running). The prospect stays
        un-enriched and will be picked up on the next enrichment run.
        """
        try:
            if self._pb_client is None:
                from .phantombuster import PhantomBusterClient
                self._pb_client = PhantomBusterClient()

            from ..config import (
                PHANTOMBUSTER_SCRAPER_AGENT_ID,
                PHANTOMBUSTER_SCRAPER_POLL_INTERVAL,
                PHANTOMBUSTER_SCRAPER_POLL_TIMEOUT,
            )
            result = self._pb_client.scrape_linkedin_profile(
                agent_id=PHANTOMBUSTER_SCRAPER_AGENT_ID,
                linkedin_url=linkedin_url,
                poll_interval=PHANTOMBUSTER_SCRAPER_POLL_INTERVAL,
                poll_timeout=PHANTOMBUSTER_SCRAPER_POLL_TIMEOUT,
            )
            if result:
                logger.info("PB scraper enriched LinkedIn profile: %s", linkedin_url)
                from ..db.credits import log_credits
                log_credits("phantombuster", "linkedin_scrape", credits=1, context=linkedin_url)
                return result

            logger.debug("PB scraper returned no data for %s (slot busy or no result)", linkedin_url)
            return {}

        except Exception as e:
            logger.warning("PB scraper enrichment failed for %s: %s", linkedin_url, e)
            return {}

    # ------------------------------------------------------------------
    # Website scraping  -  company description
    # ------------------------------------------------------------------

    def _scrape_company(self, website: str) -> dict:
        """
        Fetches the company's About page and extracts a short description.
        Falls back to the homepage meta description if no About page exists.
        Free, no API key required.
        """
        base = website.rstrip("/")
        if not base.startswith("http"):
            base = f"https://{base}"

        description = None

        # Try /about first, then homepage
        for path in ["/about", "/about-us", "/company", ""]:
            url = f"{base}{path}"
            try:
                try:
                    resp = self.client.get(url, timeout=10, follow_redirects=True)
                except httpx.ConnectError:
                    # Retry without SSL verification for sites with self-signed certs
                    resp = self._client_no_ssl.get(url)
                if resp.status_code != 200:
                    continue
                html = resp.text

                # 1. Try meta description tags (handle any attribute order)
                description = _extract_meta(html, "description")
                if not description:
                    description = _extract_meta(html, "og:description")
                if not description:
                    description = _extract_meta(html, "twitter:description")
                if description and len(description) > 40:
                    break

                # 2. Fallback: strip scripts/styles, find first real paragraph
                clean = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ", html, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<[^>]+>", " ", clean)
                clean = re.sub(r"\s+", " ", clean).strip()
                sentences = re.findall(r"[A-Z][^.!?]{50,350}[.!?]", clean)
                # Skip nav-like junk: require at least one space (multi-word sentence)
                meaningful = [s.strip() for s in sentences if s.count(" ") >= 6]
                if meaningful:
                    description = meaningful[0]
                    break

            except Exception as e:
                logger.debug(f"Website scrape failed for {url}: {e}")
                continue

            time.sleep(0.5)  # polite delay between page fetches

        if description:
            description = description[:300].rsplit(" ", 1)[0]
            logger.info(f"Scraped company description from {base}")
            return {"company_description": description}

        return {}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _merge(base: dict, updates: dict) -> dict:
    """Merges updates into base, only filling in empty/None values."""
    result = dict(base)
    for k, v in updates.items():
        if v and not result.get(k):
            result[k] = v
    return result


def _extract_meta(html: str, name: str) -> str:
    """
    Extracts a meta tag content value regardless of attribute order.
    Handles both name= and property= variants (for og:/twitter: tags).
    """
    # Find any <meta> tag that contains the right name/property value
    escaped = re.escape(name)
    pattern = r'<meta[^>]+(?:name|property)=["\']' + escaped + r'["\'][^>]*>'
    tag_match = re.search(pattern, html, re.IGNORECASE)
    if not tag_match:
        return ""
    tag = tag_match.group(0)
    # Extract content= from that tag
    content = re.search(r'content=["\']([^"\']{10,})["\']', tag, re.IGNORECASE)
    return content.group(1).strip() if content else ""


def _extract_domain(url: str) -> str:
    """Extracts bare domain from a URL (e.g. https://www.shell.com/about → shell.com)."""
    url = re.sub(r"https?://(www\.)?", "", url).split("/")[0].strip()
    return url if "." in url else ""
