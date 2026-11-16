import os
import time
import logging
import httpx
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

APOLLO_BASE = "https://api.apollo.io/v1"
# Apollo free tier: 50 requests/minute
RATE_LIMIT_DELAY = 1.2  # seconds between requests to stay safe


class ApolloClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("APOLLO_API_KEY")
        if not self.api_key:
            raise ValueError("APOLLO_API_KEY is not set.")
        self.client = httpx.Client(timeout=30)
        self._last_request_time = 0.0

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _post(self, endpoint: str, payload: dict, retries: int = 3) -> dict:
        url = f"{APOLLO_BASE}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": self.api_key,
        }
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.client.post(url, json=payload, headers=headers)
                if resp.status_code == 429:
                    wait = min(60 * (attempt + 1), 90)  # cap at 90s
                    logger.warning(f"Apollo rate limit hit. Waiting {wait}s before retry.")
                    time.sleep(wait)
                    continue
                if resp.status_code == 422:
                    body = resp.text[:200]
                    logger.error(f"Apollo 422 on {endpoint}: {body}")
                    raise RuntimeError(
                        f"Apollo rejected the request (422). This usually means "
                        f"your credits are exhausted or the API key lacks permissions. "
                        f"Check your Apollo dashboard. Detail: {body}"
                    )
                resp.raise_for_status()
                try:
                    return resp.json()
                except Exception:
                    raise RuntimeError(f"Apollo returned non-JSON response ({resp.status_code}): {resp.text[:200]}")
            except httpx.HTTPStatusError as e:
                if attempt == retries - 1:
                    raise
                backoff = 2 ** attempt
                logger.warning(f"Apollo request failed (attempt {attempt + 1}): {e}. Retrying in {backoff}s.")
                time.sleep(backoff)
        raise RuntimeError("Apollo API request failed after all retries.")

    def search_people(
        self,
        titles: Optional[list[str]] = None,
        seniorities: Optional[list[str]] = None,
        industries: Optional[list[str]] = None,
        employee_ranges: Optional[list[str]] = None,
        locations: Optional[list[str]] = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict:
        payload = {
            "page": page,
            "per_page": per_page,
            "contact_email_status": ["verified"],
        }

        if titles:
            payload["person_titles"] = titles
        if seniorities:
            payload["person_seniorities"] = seniorities
        if industries:
            payload["q_organization_keyword_tags"] = industries
        if locations:
            payload["person_locations"] = locations
        if employee_ranges:
            payload["organization_num_employees_ranges"] = employee_ranges

        result = self._post("/mixed_people/api_search", payload)
        return result

    def _reveal_people(self, ids: list[str], batch_size: int = 10) -> list[dict]:
        """Reveal full contact details (including email) for a list of Apollo person IDs.
        Uses export credits: 1 credit per unique contact revealed.

        Aborts early if consecutive batches return 0 emails — prevents
        burning credits when Apollo is throttling or the key lost reveal
        permissions.
        """
        revealed = []
        consecutive_empty = 0
        for i in range(0, len(ids), batch_size):
            batch = ids[i:i + batch_size]
            payload = {
                # Audit 2026-05-05: MUST be True. When False, Apollo strips
                # the email field entirely for contacts that only have a
                # personal email on file. This caused 734 credits to be
                # consumed with zero emails returned.
                "reveal_personal_emails": True,
                "details": [{"id": pid} for pid in batch],
            }
            result = self._post("/people/bulk_match", payload)
            matches = result.get("matches")
            if matches is None:
                logger.warning(
                    "Apollo bulk_match returned no 'matches' key (batch %d-%d). "
                    "Response keys: %s. Credits may have been consumed without data.",
                    i, i + len(batch), list(result.keys()),
                )
                consecutive_empty += 1
            else:
                # Check if this batch actually has emails
                batch_has_email = any(_extract_email(m) for m in matches)
                if batch_has_email:
                    consecutive_empty = 0
                else:
                    consecutive_empty += 1
                revealed.extend(matches)

            # If 2 consecutive batches returned 0 emails, Apollo is likely
            # throttling or the key lost permissions. Stop immediately.
            if consecutive_empty >= 2:
                remaining = len(ids) - (i + len(batch))
                logger.error(
                    "Apollo reveal: %d consecutive batches returned 0 emails. "
                    "Aborting early to save credits. %d contacts revealed, "
                    "%d credits saved.",
                    consecutive_empty, len(revealed), remaining,
                )
                break

        return revealed

    def search_all_pages(
        self,
        titles: Optional[list[str]] = None,
        seniorities: Optional[list[str]] = None,
        industries: Optional[list[str]] = None,
        employee_ranges: Optional[list[str]] = None,
        locations: Optional[list[str]] = None,
        limit: int = 100,
        existing_apollo_ids: Optional[set[str]] = None,
        db=None,
    ) -> list[dict]:
        # Step 1: collect IDs of people who have emails
        ids_to_reveal = []
        cached_results = []
        skipped_existing = 0
        skipped_cached = 0
        page = 1
        per_page = min(25, limit)
        _existing = existing_apollo_ids or set()

        # Load reveal cache IDs so we can serve from cache instead of
        # spending credits again. Uses its own session so a failure here
        # can never corrupt the caller's transaction.
        _cached_ids: dict[str, dict] = {}
        if db is not None:
            from ..db.database import SessionLocal as _CacheSessionLocal
            _cache_read_db = _CacheSessionLocal()
            try:
                from ..db.models import ApolloRevealCache
                for row in _cache_read_db.query(ApolloRevealCache).all():
                    _cached_ids[row.apollo_id] = row.raw_json
            except Exception:
                pass  # table might not exist yet on first run
            finally:
                _cache_read_db.close()

        while len(ids_to_reveal) + len(cached_results) < limit:
            result = self.search_people(
                titles=titles,
                seniorities=seniorities,
                industries=industries,
                employee_ranges=employee_ranges,
                locations=locations,
                page=page,
                per_page=per_page,
            )
            people = result.get("people", [])
            if not people:
                break

            for person in people:
                if person.get("has_email"):
                    pid = person["id"]
                    # Skip contacts already saved as prospects
                    if pid in _existing:
                        skipped_existing += 1
                        continue
                    # Serve from cache — no credit cost
                    if pid in _cached_ids:
                        cached_results.append(_cached_ids[pid])
                        skipped_cached += 1
                        continue
                    ids_to_reveal.append(pid)
                if len(ids_to_reveal) + len(cached_results) >= limit:
                    break

            total = result.get("total_entries", result.get("pagination", {}).get("total_entries", 0))
            if page * per_page >= total:
                break
            page += 1

        if skipped_existing:
            logger.info(f"Skipped {skipped_existing} contacts already saved as prospects.")
        if skipped_cached:
            logger.info(f"Loaded {skipped_cached} contacts from reveal cache (0 credits).")

        if not ids_to_reveal and not cached_results:
            logger.info("No new contacts to reveal — all matches already exist.")
            return []

        # Step 2: reveal NEW contacts via the centralized cache-aware function.
        # reveal_with_cache handles: cache check, reveal, per-row cache write,
        # and credit logging. This replaced the old manual reveal+cache block
        # that crashed after 18 rows due to all-or-nothing commits.
        revealed = []
        if ids_to_reveal:
            from ..db.database import SessionLocal
            revealed = reveal_with_cache(self, ids_to_reveal, SessionLocal)

        # Combine cached + newly revealed
        all_revealed = cached_results + revealed

        # Step 3: normalize, keeping only contacts with verified emails
        prospects = []
        for person in all_revealed:
            email = _extract_email(person)
            if not email:
                logger.info(f"Skipping {person.get('name')}  -  no verified email after reveal.")
                continue
            prospects.append(_normalize_person(person, email))

        return prospects

    def close(self):
        self.client.close()


def _extract_email(person: dict) -> Optional[str]:
    email = person.get("email")
    if email and "@" in email:
        return email
    for contact in person.get("contact_emails", []):
        if contact.get("email") and "@" in contact["email"]:
            return contact["email"]
    return None


def _normalize_person(person: dict, email: str) -> dict:
    org = person.get("organization", {}) or {}
    return {
        "apollo_id": person.get("id"),
        "first_name": person.get("first_name", ""),
        "last_name": person.get("last_name", person.get("last_name_obfuscated", "")),
        "email": email,
        "title": person.get("title", ""),
        "company": org.get("name", person.get("organization_name", "")),
        "industry": org.get("industry", ""),
        "headcount": org.get("estimated_num_employees", org.get("num_employees")),
        "location": _build_location(person),
        "linkedin_url": person.get("linkedin_url", ""),
        "company_website": org.get("website_url", ""),
    }


def _build_location(person: dict) -> str:
    parts = [
        person.get("city"),
        person.get("state"),
        person.get("country"),
    ]
    return ", ".join(p for p in parts if p)


# ── System-wide reveal cache ────────────────────────────────────────────────
# Every code path that needs to reveal Apollo contacts MUST go through
# reveal_with_cache(). Direct calls to _reveal_people() bypass the cache
# and waste credits.


import json as _json


def _sanitize_json(obj):
    """Round-trip through JSON serialization to coerce non-serializable types.

    Converts datetimes, bytes, Decimals etc. to strings so the result
    is safe to store in a JSON/JSONB column on both SQLite and Postgres.
    """
    return _json.loads(_json.dumps(obj, default=str))


def reveal_with_cache(
    apollo_client: ApolloClient,
    apollo_ids: list[str],
    db_session_factory,
) -> list[dict]:
    """Reveal Apollo contacts with per-row caching.

    This is the ONLY function external code should call to reveal contacts.
    It is not tied to any feature — campaigns, conferences, CLI, and any
    future code all go through this single path.

    Returns the full Apollo person dicts (from cache or fresh reveal).
    """
    if not apollo_ids:
        return []

    # Dedupe input IDs
    unique_ids = list(dict.fromkeys(apollo_ids))

    # 1. Check cache for existing reveals
    cache_hits: dict[str, dict] = {}
    try:
        cache_db = db_session_factory()
        try:
            from ..db.models import ApolloRevealCache
            # Query only the IDs we need, not the entire cache
            existing = (
                cache_db.query(ApolloRevealCache)
                .filter(ApolloRevealCache.apollo_id.in_(unique_ids))
                .all()
            )
            for row in existing:
                if row.raw_json:
                    cache_hits[row.apollo_id] = row.raw_json
        except Exception as exc:
            logger.warning("reveal_with_cache: cache read failed (will reveal all): %s", exc)
        finally:
            cache_db.close()
    except Exception as exc:
        logger.warning("reveal_with_cache: session factory failed: %s", exc)

    cache_miss_ids = [aid for aid in unique_ids if aid not in cache_hits]

    # 2. Reveal only uncached IDs
    revealed = []
    if cache_miss_ids:
        logger.info(
            "reveal_with_cache: revealing %d new contacts (%d cached, 0 credits for cached)",
            len(cache_miss_ids), len(cache_hits),
        )
        revealed = apollo_client._reveal_people(cache_miss_ids)

        # 3. Per-row cache write — one bad row cannot kill the rest
        cached_ok = 0
        cached_fail = 0
        for person in revealed:
            aid = person.get("id")
            if not aid:
                continue
            try:
                row_db = db_session_factory()
                try:
                    from ..db.models import ApolloRevealCache
                    safe_json = _sanitize_json(person)
                    row_db.merge(ApolloRevealCache(
                        apollo_id=aid,
                        email=_extract_email(person) or "",
                        raw_json=safe_json,
                    ))
                    row_db.commit()
                    cached_ok += 1
                except Exception as exc:
                    logger.warning("reveal_with_cache: cache write failed for %s: %s", aid, exc)
                    try:
                        row_db.rollback()
                    except Exception:
                        pass
                    cached_fail += 1
                finally:
                    row_db.close()
            except Exception as exc:
                logger.warning("reveal_with_cache: session create failed for %s: %s", aid, exc)
                cached_fail += 1

        # 4. Log credit usage (only for NEW reveals)
        if revealed:
            try:
                from ..db.credits import log_credits
                log_credits("apollo", "reveal", credits=len(revealed))
            except Exception:
                pass

        logger.info(
            "apollo_reveal_cache: requested=%d cache_hit=%d cache_miss=%d "
            "revealed=%d cached_ok=%d cached_fail=%d",
            len(unique_ids), len(cache_hits), len(cache_miss_ids),
            len(revealed), cached_ok, cached_fail,
        )
    elif cache_hits:
        logger.info(
            "apollo_reveal_cache: requested=%d cache_hit=%d cache_miss=0 (0 credits)",
            len(unique_ids), len(cache_hits),
        )

    # 5. Combine cached + newly revealed
    result = list(cache_hits.values()) + revealed
    return result


def backfill_reveal_cache(db_session_factory) -> int:
    """Populate ApolloRevealCache from existing Prospect rows.

    Returns the number of rows backfilled. Safe to run multiple times
    (skips IDs already in cache).
    """
    db = db_session_factory()
    try:
        from ..db.models import ApolloRevealCache, Prospect

        # Load existing cache IDs
        cached_ids = set()
        try:
            for row in db.query(ApolloRevealCache.apollo_id).all():
                cached_ids.add(row[0])
        except Exception:
            pass

        # Find prospects with apollo_id not in cache
        prospects = (
            db.query(Prospect)
            .filter(Prospect.apollo_id.isnot(None))
            .all()
        )

        backfilled = 0
        for p in prospects:
            if p.apollo_id in cached_ids:
                continue
            try:
                row_db = db_session_factory()
                try:
                    row_db.merge(ApolloRevealCache(
                        apollo_id=p.apollo_id,
                        email=p.email or "",
                        raw_json=_sanitize_json({
                            "id": p.apollo_id,
                            "email": p.email,
                            "first_name": getattr(p, "first_name", ""),
                            "last_name": getattr(p, "last_name", ""),
                            "title": getattr(p, "title", ""),
                            "organization": {"name": getattr(p, "company", "")},
                            "linkedin_url": getattr(p, "linkedin_url", ""),
                        }),
                    ))
                    row_db.commit()
                    backfilled += 1
                except Exception as exc:
                    logger.warning("backfill: failed for %s: %s", p.apollo_id, exc)
                    try:
                        row_db.rollback()
                    except Exception:
                        pass
                finally:
                    row_db.close()
            except Exception:
                pass

        logger.info("backfill_reveal_cache: %d prospects backfilled into cache", backfilled)
        return backfilled
    finally:
        db.close()
