"""
Live API quota and rate-limit status for all connected services.

Each service fetches real data from its own API (no hardcoded numbers).
Results are cached in-memory for 5 minutes so refreshing the dashboard
does not hammer external APIs on every page load.

Add ?refresh=1 to bust the cache and force a fresh fetch.
"""
import os
import time
import logging
import json as _json
from typing import Optional

import httpx
from fastapi import APIRouter, Query
from dotenv import load_dotenv

from ..config import MODEL_FAST, EMAIL_GEN_MAX_TOKENS

load_dotenv()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/limits", tags=["limits"])

# Server-side cache: refreshed at most every 5 minutes
_cache: dict = {"ts": 0.0, "data": None}
_CACHE_TTL = 300  # seconds


@router.get("/")
def get_api_limits(refresh: int = Query(default=0)):
    """
    Returns live quota/rate-limit data for Reacher, Apollo, and Anthropic.
    Pass ?refresh=1 to force a fresh fetch (busts the 5-minute cache).
    """
    global _cache
    if not refresh and _cache["data"] and (time.time() - _cache["ts"]) < _CACHE_TTL:
        return {**_cache["data"], "cached": True, "cache_age_s": int(time.time() - _cache["ts"])}

    data = {
        "services": [
            _fetch_reacher(),
            _fetch_apollo(),
            _fetch_anthropic(),
        ],
        "cached": False,
        "cache_age_s": 0,
    }
    _cache = {"ts": time.time(), "data": data}
    return data


# ── Reacher (self-hosted email verification) ─────────────────────────────────

def _fetch_reacher() -> dict:
    from ..config import REACHER_DEFAULT_URL
    base_url = os.getenv("REACHER_BASE_URL", "").strip() or REACHER_DEFAULT_URL
    try:
        resp = httpx.get(f"{base_url}/", timeout=10)
        connected = resp.status_code < 500
        return {
            "id": "reacher",
            "name": "Reacher",
            "connected": connected,
            "quotas": [
                {
                    "metric": "Email validations",
                    "remaining": None,
                    "total": None,
                    "used": None,
                    "period": "unlimited (self-hosted)",
                    "tooltip": (
                        "Reacher is self-hosted on your VPS  -  no per-email credits. "
                        "Validation capacity is limited only by SMTP rate limits."
                    ),
                }
            ],
        }
    except Exception as e:
        logger.warning("Reacher health check failed: %s", e)
        return _error("reacher", "Reacher", "Health check failed. See server logs.")


# ── Apollo.io ─────────────────────────────────────────────────────────────────

def _fetch_apollo() -> dict:
    api_key = os.getenv("APOLLO_API_KEY", "").strip()
    if not api_key:
        return _not_configured("apollo", "Apollo.io")
    try:
        from ..db.credits import get_summary

        # A free search call (no export credits consumed)  -  used only to read
        # rate-limit headers from Apollo's response.
        resp = httpx.post(
            "https://api.apollo.io/v1/mixed_people/api_search",
            json={"page": 1, "per_page": 1},
            headers={
                "Content-Type": "application/json",
                "X-Api-Key": api_key,
                "Cache-Control": "no-cache",
            },
            timeout=10,
        )
        hdrs = resp.headers

        # Apollo returns rate-limit windows in multiple header formats across plans.
        # Try minute-window first, fall back to standard headers.
        req_limit = (
            _safe_int(hdrs.get("x-rate-limit-requests-minute"))
            or _safe_int(hdrs.get("x-ratelimit-limit"))
        )
        req_remaining = (
            _safe_int(hdrs.get("x-rate-limit-requests-remaining-minute"))
            or _safe_int(hdrs.get("x-ratelimit-remaining"))
        )
        hr_limit = _safe_int(hdrs.get("x-rate-limit-requests-hour"))
        hr_remaining = _safe_int(hdrs.get("x-rate-limit-requests-remaining-hour"))

        # Export credits: read from our own credit_logs (we log every reveal call).
        # Apollo free tier = 50 export credits/month; paid tiers are higher.
        # We report what we've consumed via our log and assume free-tier baseline.
        from ..config import APOLLO_MONTHLY_CREDITS
        usage = get_summary(days=30)
        credits_used_month = usage.get("apollo", 0)
        credits_remaining = max(0, APOLLO_MONTHLY_CREDITS - credits_used_month)

        quotas = [
            {
                "metric": "Export credits (this month)",
                "remaining": credits_remaining,
                "total": APOLLO_MONTHLY_CREDITS,
                "used": credits_used_month,
                "period": "monthly",
                "tooltip": (
                    f"{credits_remaining} of {APOLLO_MONTHLY_CREDITS} export credits left this month. "
                    "1 credit reveals 1 contact's email. Resets on billing date. "
                    "(Set APOLLO_MONTHLY_CREDITS env var to match your plan.)"
                ),
            }
        ]

        if req_limit is not None and req_remaining is not None:
            pct_used = round((req_limit - req_remaining) / req_limit * 100) if req_limit else 0
            quotas.append({
                "metric": "Search requests",
                "remaining": req_remaining,
                "total": req_limit,
                "used": req_limit - req_remaining,
                "period": "per minute",
                "tooltip": (
                    f"{req_remaining}/{req_limit} prospect searches left this minute "
                    f"({pct_used}% used). Each call to find new prospects uses 1 request."
                ),
            })
        else:
            # Headers not present on this plan  -  show known free-tier limits
            quotas.append({
                "metric": "Search rate limit",
                "remaining": None,
                "total": None,
                "used": None,
                "period": "per minute",
                "tooltip": "Apollo free tier: 50 searches/min, 200/hr, 600/24hr. No email reveal on free search.",
            })

        if hr_limit is not None and hr_remaining is not None:
            quotas.append({
                "metric": "Search requests",
                "remaining": hr_remaining,
                "total": hr_limit,
                "used": hr_limit - hr_remaining,
                "period": "per hour",
                "tooltip": (
                    f"{hr_remaining}/{hr_limit} prospect searches left this hour."
                ),
            })

        return {"id": "apollo", "name": "Apollo.io", "connected": True, "quotas": quotas}

    except Exception as e:
        logger.warning("Apollo limits fetch failed: %s", e)
        return _error("apollo", "Apollo.io", "Could not fetch limits. See server logs.")


# ── Anthropic (Claude) ────────────────────────────────────────────────────────

def _fetch_anthropic() -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return _not_configured("anthropic", "Anthropic (Claude)")
    try:
        import anthropic as _anthropic

        client = _anthropic.Anthropic(api_key=api_key)
        # Minimal call (1 input token, 1 output token) to read rate-limit headers.
        # Cost: ~$0.000001  -  negligible. Cached for 5 minutes server-side.
        raw = client.messages.with_raw_response.create(
            model=MODEL_FAST,
            max_tokens=1,
            messages=[{"role": "user", "content": "1"}],
        )
        hdrs = raw.headers

        req_limit     = _safe_int(hdrs.get("anthropic-ratelimit-requests-limit"))
        req_remaining = _safe_int(hdrs.get("anthropic-ratelimit-requests-remaining"))
        req_reset     = hdrs.get("anthropic-ratelimit-requests-reset", "")
        tok_limit     = _safe_int(hdrs.get("anthropic-ratelimit-tokens-limit"))
        tok_remaining = _safe_int(hdrs.get("anthropic-ratelimit-tokens-remaining"))
        tok_reset     = hdrs.get("anthropic-ratelimit-tokens-reset", "")

        # Estimate how many full email sequences fit in remaining token budget.
        # EMAIL_GEN_MAX_TOKENS is the output cap per generation call.
        seqs_from_tok = (tok_remaining // EMAIL_GEN_MAX_TOKENS) if tok_remaining else None
        seqs_from_req = req_remaining  # 1 request = 1 sequence

        quotas = []

        if req_limit is not None:
            seqs = min(seqs_from_tok or seqs_from_req or 0, seqs_from_req or 9999)
            reset_note = f" Resets {req_reset}." if req_reset else ""
            quotas.append({
                "metric": "API requests",
                "remaining": req_remaining,
                "total": req_limit,
                "used": (req_limit - req_remaining) if req_remaining is not None else None,
                "period": "per minute",
                "reset": req_reset,
                "tooltip": (
                    f"{req_remaining}/{req_limit} API calls available this minute. "
                    f"1 call = 1 email sequence (3 emails). "
                    f"Approx {seqs} sequences can be generated right now.{reset_note}"
                ),
            })

        if tok_limit is not None:
            reset_note = f" Resets {tok_reset}." if tok_reset else ""
            quotas.append({
                "metric": "Tokens",
                "remaining": tok_remaining,
                "total": tok_limit,
                "used": (tok_limit - tok_remaining) if tok_remaining is not None else None,
                "period": "per minute",
                "reset": tok_reset,
                "tooltip": (
                    f"{tok_remaining:,}/{tok_limit:,} tokens available this minute. "
                    f"Each email sequence uses up to {EMAIL_GEN_MAX_TOKENS:,} output tokens. "
                    f"Approx {seqs_from_tok} sequences fit in the remaining budget.{reset_note}"
                ),
            })

        if not quotas:
            quotas = [{
                "metric": "Rate limit",
                "remaining": None,
                "total": None,
                "used": None,
                "period": "per minute",
                "tooltip": "Connected. Rate limit headers not returned for this plan tier.",
            }]

        return {"id": "anthropic", "name": "Anthropic (Claude)", "connected": True, "quotas": quotas}

    except Exception as e:
        logger.warning("Anthropic limits fetch failed: %s", e)
        return _error("anthropic", "Anthropic (Claude)", "Could not fetch limits. See server logs.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_int(val) -> Optional[int]:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _not_configured(service_id: str, name: str) -> dict:
    return {
        "id": service_id,
        "name": name,
        "connected": False,
        "quotas": [],
        "error": "API key not configured.",
    }


def _error(service_id: str, name: str, msg: str) -> dict:
    return {
        "id": service_id,
        "name": name,
        "connected": False,
        "quotas": [],
        "error": msg,
    }
