"""
Real-time project search for prospect companies.

Design principle: 0% hallucination.
- We only ever reference facts returned by DuckDuckGo's live web search.
- Claude is given the raw snippet and told to rephrase it  -  never recall from memory.
- If no relevant result is found within the lookback window, we return None and the
  email generator skips the project mention entirely.

2026-04-09: Migrated from Tavily → DuckDuckGo (ddgs library). DDG needs no API key
and is free, but throttles aggressively  -  we wrap calls with a small backoff.
"""
import os
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from dotenv import load_dotenv
from ..config import (
    MODEL_FAST,
    PROJECT_SEARCH_LOOKBACK_DAYS,
    PROJECT_SEARCH_MAX_RESULTS,
    PROJECT_SNIPPET_MAX_CHARS,
    PROJECT_REPHRASE_MAX_TOKENS,
)

import anthropic as _anthropic

load_dotenv()
logger = logging.getLogger(__name__)

LOOKBACK_DAYS = PROJECT_SEARCH_LOOKBACK_DAYS

# Lazy-initialized Anthropic client  -  reuse TCP connection across calls
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _google_news_rss(query: str, max_results: int) -> list[dict]:
    """Search Google News via its public RSS feed. No API key needed.

    Returns dicts with title, body, url, date keys matching DDG format.
    """
    import urllib.parse
    import xml.etree.ElementTree as ET
    import httpx

    encoded = urllib.parse.quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
        items = root.findall(".//item")
        results = []
        for item in items[:max_results]:
            title = (item.findtext("title") or "").strip()
            # Google News RSS puts the snippet in <description> as HTML
            desc = (item.findtext("description") or "").strip()
            # Strip HTML tags from description
            import re
            body = re.sub(r"<[^>]+>", "", desc).strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            link = (item.findtext("link") or "").strip()
            results.append({
                "title": title,
                "body": body,
                "url": link,
                "date": pub_date,
                "source": "Google News",
            })
        return results
    except Exception as e:
        logger.debug(f"Google News RSS failed: {e}")
        return []


def _ddg_news(query: str, max_results: int, company: str = "") -> list[dict]:
    """Run a DDG news search with fallback to Google News RSS."""
    try:
        from ddgs import DDGS
    except ImportError:  # pragma: no cover - legacy package name
        from duckduckgo_search import DDGS  # type: ignore

    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.news(query=query, max_results=max_results)) or []
                if results:
                    return results
        except Exception as e:  # noqa: BLE001  -  DDG raises a variety of types
            last_err = e
            if attempt == 0:
                time.sleep(1.5)
                continue

    if last_err:
        logger.debug(f"DDG failed for '{query[:60]}...': {last_err}, trying Google News RSS")

    # Fallback: Google News RSS with a simpler query that works better
    # with Google's search. The full DDG query with all the OR terms
    # is too restrictive for RSS.
    simple_query = f"{company} project contract" if company else query
    results = _google_news_rss(simple_query, max_results)
    if results:
        return results

    if last_err:
        logger.warning(f"Project search failed for '{query[:60]}...': DDG and Google News both returned nothing")
    return []


def find_recent_project(company: str) -> Optional[str]:
    """
    Searches the web for a recent large project announcement by the company.

    Returns a one-sentence, factual project reference string ready to drop into
    an email (e.g. "Saw you recently broke ground on the Al Reeman III project
    in Abu Dhabi"), or None if nothing relevant and recent was found.

    Never invents or infers  -  only uses what DuckDuckGo returns.
    """
    if not company or not company.strip():
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    query = (
        f'"{company}" '
        f'(project OR contract OR "breaking ground" OR awarded OR construction OR development OR expansion)'
    )

    results = _ddg_news(query, PROJECT_SEARCH_MAX_RESULTS, company=company)
    if not results:
        logger.debug(f"No DDG results for '{company}'")
        return None

    # Filter to results that actually mention the company name and a project keyword.
    # Use the brand name (first significant word) for matching since news articles
    # rarely use the full legal name (e.g. "KBR" not "KBR, Inc.").
    company_lower = company.lower()
    _brand = company_lower.split(",")[0].split("(")[0].strip()
    # For multi-word names like "Orascom Construction", also try first word only
    _brand_short = _brand.split()[0] if len(_brand.split()) > 1 else _brand
    project_keywords = {
        "project", "contract", "awarded", "construction", "development",
        "expansion", "breaking ground", "groundbreaking", "pipeline",
        "facility", "plant", "terminal", "refinery", "site", "win",
        "won", "secured", "secures", "signed", "signing",
    }

    best = None
    for r in results:
        title = (r.get("title") or "").lower()
        body = (r.get("body") or "").lower()
        combined = title + " " + body

        if _brand not in combined and _brand_short not in combined:
            continue
        if not any(kw in combined for kw in project_keywords):
            continue

        # DDG news always returns a `date` field  -  drop anything older than the window.
        pub_date = r.get("date")
        if pub_date:
            try:
                normalized = pub_date.replace("Z", "+00:00")
                pd = datetime.fromisoformat(normalized)
                if pd.tzinfo is None:
                    pd = pd.replace(tzinfo=timezone.utc)
                if pd < cutoff:
                    continue  # too old
            except Exception:
                pass  # can't parse date  -  allow it through

        best = r
        break

    if not best:
        logger.debug(f"No relevant recent project found for '{company}'")
        return None

    # Extract the most useful snippet  -  title + first N chars of body
    title = (best.get("title") or "").strip()
    snippet = (best.get("body") or "")[:PROJECT_SNIPPET_MAX_CHARS].strip()
    source_text = f"Title: {title}\nSnippet: {snippet}"

    # Ask Claude to rephrase it into one natural sentence.
    # Strict instruction: use only what's in the snippet  -  no recall from training data.
    reference = _rephrase_with_claude(company, source_text)
    if reference:
        logger.info(f"Project reference found for '{company}': {reference[:80]}…")
    return reference


def _rephrase_with_claude(company: str, source_text: str) -> Optional[str]:
    """
    Takes a raw search snippet and rephrases it into one sentence for email use.
    Claude is instructed to use ONLY the provided text  -  no hallucination possible.
    """

    prompt = f"""You are helping write a cold email. Below is a real news snippet about {company}.

Your task: write exactly ONE short sentence (max 15 words) that naturally references this project.
The sentence should sound like something you'd casually drop into an email, e.g.:
"Saw you recently broke ground on [project] in [location]" or
"Noticed {company} just landed the [project] contract"

Rules  -  read carefully:
- Use ONLY information from the snippet below. Do not add anything from your training data.
- If the snippet does not clearly describe a real, specific project, reply with exactly: SKIP
- Do not mention safety or Example  -  just the project reference.
- No em dashes. No "I noticed that". Start directly: "Saw", "Noticed", "Heard", etc.

Snippet:
{source_text}

Reply with the one sentence only, or SKIP."""

    try:
        msg = _get_client().messages.create(
            model=MODEL_FAST,
            max_tokens=PROJECT_REPHRASE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        result = msg.content[0].text.strip() if msg.content else ""
        if not result or result.upper() == "SKIP":
            return None
        # Validate output is actually a short sentence as requested
        word_count = len(result.split())
        if word_count > 25:
            logger.warning(f"Claude rephrase exceeded word limit ({word_count} words), skipping: {result[:80]}…")
            return None
        return result
    except Exception as e:
        logger.warning(f"Claude rephrase failed: {e}")
        return None
