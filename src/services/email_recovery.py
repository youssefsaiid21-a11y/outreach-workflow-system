"""
Email recovery for prospects whose email bounced or was flagged invalid.

Recovery chain (in priority order):
  1. Hunter.io email-finder   -  direct name + domain lookup
  2. DB pattern inference     -  detect the email format from valid contacts at
                               the same company already in your database, then
                               apply that pattern to the bounced prospect
  3. Hunter domain-search     -  ask Hunter for the domain's email pattern

Each candidate email is validated with Reacher before being saved.
"""
import re
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

HUNTER_BASE = "https://api.hunter.io/v2"

# All patterns Hunter uses, mapped to a builder lambda (first, last) -> local_part
_PATTERNS = {
    "{first}.{last}":  lambda f, l: f"{f}.{l}",
    "{first}{last}":   lambda f, l: f"{f}{l}",
    "{f}.{last}":      lambda f, l: f"{f[0]}.{l}",
    "{f}{last}":       lambda f, l: f"{f[0]}{l}",
    "{first}":         lambda f, l: f,
    "{last}.{first}":  lambda f, l: f"{l}.{f}",
    "{last}{first}":   lambda f, l: f"{l}{f}",
    "{f}.{l}":         lambda f, l: f"{f[0]}.{l[0]}",
    "{last}":          lambda f, l: l,
}


# ── Pattern helpers ────────────────────────────────────────────────────────────

def _domain_from_email(email: str) -> str:
    parts = email.strip().lower().split("@")
    return parts[1] if len(parts) == 2 else ""


def _detect_pattern(first: str, last: str, local: str) -> Optional[str]:
    """
    Given a name and the local part of a known-good email, return the matching
    Hunter-style pattern string, or None if no pattern matches.
    """
    f = first.strip().lower()
    l = last.strip().lower()
    local = local.strip().lower()
    if not f or not l or not local:
        return None
    for pattern, builder in _PATTERNS.items():
        try:
            if builder(f, l) == local:
                return pattern
        except (IndexError, AttributeError):
            continue
    return None


def _apply_pattern(pattern: str, first: str, last: str) -> Optional[str]:
    """Apply a Hunter pattern to a name and return the local part."""
    builder = _PATTERNS.get(pattern)
    if not builder:
        return None
    try:
        f = first.strip().lower()
        l = last.strip().lower()
        if not f or not l:
            return None
        return builder(f, l)
    except (IndexError, AttributeError):
        return None


# ── Recovery strategies ────────────────────────────────────────────────────────

def _hunter_find_email(
    first: str, last: str, domain: str, hunter_key: str
) -> Optional[str]:
    """
    Call Hunter email-finder for a specific person at a domain.
    Returns the found email or None.
    """
    if not hunter_key or not domain or not first or not last:
        return None
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{HUNTER_BASE}/email-finder",
                params={
                    "domain": domain,
                    "first_name": first,
                    "last_name": last,
                    "api_key": hunter_key,
                },
            )
        if resp.status_code == 429:
            logger.warning("Hunter email-finder rate limit hit.")
            return None
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json().get("data", {})
        found = data.get("email")
        if found:
            logger.info(f"Hunter found candidate email for {first} {last}: {found}")
        return found or None
    except Exception as e:
        logger.warning(f"Hunter email-finder failed for {first} {last} @ {domain}: {e}")
        return None


def _hunter_domain_pattern(domain: str, hunter_key: str) -> Optional[str]:
    """
    Call Hunter domain-search to learn the email pattern for a domain.
    Returns a Hunter pattern string like '{first}.{last}' or None.
    """
    if not hunter_key or not domain:
        return None
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{HUNTER_BASE}/domain-search",
                params={"domain": domain, "api_key": hunter_key, "limit": 5},
            )
        if resp.status_code in (429, 404):
            return None
        resp.raise_for_status()
        pattern = resp.json().get("data", {}).get("pattern")
        if pattern:
            logger.info(f"Hunter domain pattern for {domain}: {pattern}")
        return pattern or None
    except Exception as e:
        logger.warning(f"Hunter domain-search failed for {domain}: {e}")
        return None


def _db_pattern_inference(first: str, last: str, domain: str, db) -> Optional[str]:
    """
    Look at valid prospects in the DB from the same domain, detect their email
    pattern, and apply it to construct a candidate for the target prospect.
    Returns a candidate email address or None.
    """
    from ..db.models import Prospect

    # Find up to 5 valid same-domain prospects with both first + last name
    samples = (
        db.query(Prospect)
        .filter(
            Prospect.email.ilike(f"%@{domain}"),
            Prospect.email_validation_status == "valid",
            Prospect.first_name.isnot(None),
            Prospect.last_name.isnot(None),
        )
        .limit(5)
        .all()
    )

    pattern_votes: dict[str, int] = {}
    for s in samples:
        local = s.email.split("@")[0]
        p = _detect_pattern(s.first_name or "", s.last_name or "", local)
        if p:
            pattern_votes[p] = pattern_votes.get(p, 0) + 1

    if not pattern_votes:
        return None

    # Use the most common pattern
    best_pattern = max(pattern_votes, key=lambda k: pattern_votes[k])
    local_part = _apply_pattern(best_pattern, first, last)
    if not local_part:
        return None

    candidate = f"{local_part}@{domain}"
    logger.info(
        f"DB pattern '{best_pattern}' (from {len(samples)} sample{'s' if len(samples)!=1 else ''}) "
        f"→ candidate: {candidate}"
    )
    return candidate


# ── Public entry point ─────────────────────────────────────────────────────────

def recover_email(
    prospect,          # Prospect ORM object
    db,                # SQLAlchemy Session
    hunter_key: str,
    validator,         # ReacherClient (already initialised by caller)
) -> Optional[str]:
    """
    Attempt to find a valid replacement email for a prospect whose email bounced.

    Returns the new validated email address, or None if all strategies fail.
    The prospect record is NOT updated here  -  the caller handles DB writes so
    they can batch-commit.
    """
    first = (prospect.first_name or "").strip()
    last  = (prospect.last_name  or "").strip()
    if not first or not last:
        logger.debug(f"Skipping recovery for {prospect.email}  -  missing name.")
        return None

    domain = _domain_from_email(prospect.email)
    if not domain:
        return None

    candidates: list[str] = []

    # Strategy 1  -  Hunter email-finder (direct lookup)
    c = _hunter_find_email(first, last, domain, hunter_key)
    if c and c.lower() != prospect.email.lower():
        candidates.append(c)

    # Strategy 2  -  DB pattern from same company
    c = _db_pattern_inference(first, last, domain, db)
    if c and c.lower() != prospect.email.lower() and c not in candidates:
        candidates.append(c)

    # Strategy 3  -  Hunter domain-search pattern
    if not candidates or (len(candidates) == 1 and candidates[0].lower() == prospect.email.lower()):
        pattern = _hunter_domain_pattern(domain, hunter_key)
        if pattern:
            local_part = _apply_pattern(pattern, first, last)
            if local_part:
                c = f"{local_part}@{domain}"
                if c.lower() != prospect.email.lower() and c not in candidates:
                    candidates.append(c)

    if not candidates:
        logger.info(f"No candidates found for {prospect.email}")
        return None

    # Validate each candidate with Reacher  -  use the first one that passes
    for candidate in candidates:
        try:
            result = validator.validate_single(candidate)
            logger.info(f"Reacher validation for {candidate}: {result['status']}")
            if result.get("is_safe"):
                return candidate
        except Exception as e:
            logger.warning(f"Reacher validation failed for {candidate}: {e}")
            continue

    return None
