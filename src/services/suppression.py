"""
Cross-system suppression and domain exclusion service.

This is the centralized suppression check layer used by multiple parts of the system.
It consolidates three types of blocking:

  1. **Campaign suppression** (UnsubscribeEntry table): Emails that have opted out or
     been manually suppressed from outreach campaigns. User-scoped (each rep has their
     own list) with global entries (user_id=NULL) applying to everyone.

  2. **Newsletter suppression** (NewsletterUnsubscribe table): Emails that clicked
     the unsubscribe link in a newsletter. Checked alongside campaign suppression
     for newsletter sends via is_newsletter_suppressed().

  3. **Domain exclusion** (ExcludedDomain table): Entire email domains blocked from
     outreach (e.g., competitor domains, "do not contact" companies, government domains).
     Categorized (competitor, client, internal, etc.) for audit purposes.

Key functions:
  - is_newsletter_suppressed(db, email) — checks both newsletter AND campaign lists
  - is_prospect_blocked(email, db, user_id) — checks suppression + domain exclusion
  - filter_prospects_for_campaign(ids, db, user_id) — batch filter for campaign launch

Performance optimization:
  The batch functions (_load_suppressed_emails, _load_excluded_domains) load the
  entire suppression set and domain map in a single query, then check each prospect
  in memory. This turns the per-prospect N+1 query pattern into O(1) lookups.
  SQLite's 999 variable limit for IN clauses is handled via CHUNK=500 batching.
"""

import logging
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db.models import (
    ExcludedDomain,
    NewsletterUnsubscribe,
    Prospect,
    UnsubscribeEntry,
    ProspectStatus,
    EmailSequence,
    SequenceStatus,
)
logger = logging.getLogger(__name__)


def is_newsletter_suppressed(db: Session, email: str) -> bool:
    """Check both newsletter-specific AND campaign-level suppression.

    Returns True if the email appears in EITHER:
      - NewsletterUnsubscribe (clicked unsubscribe link in a newsletter)
      - UnsubscribeEntry (manually suppressed from campaigns)

    This dual check ensures that someone who unsubscribed from campaigns
    also doesn't receive newsletters, and vice versa. Centralised here
    to avoid the circular import that previously existed between
    newsletters.py and newsletter_sender.py.
    """
    if NewsletterUnsubscribe.is_suppressed(db, email):
        return True
    if db.query(UnsubscribeEntry).filter(UnsubscribeEntry.email == email).first() is not None:
        return True
    return False


# ── Domain exclusion ─────────────────────────────────────────────────────────


def is_domain_excluded(email: str, db: Session) -> bool:
    """Check if the prospect's email domain is on the exclusion list."""
    if "@" not in email:
        return False
    domain = email.split("@", 1)[1].strip().lower()
    return db.query(ExcludedDomain).filter_by(domain=domain).first() is not None


def get_excluded_domains(db: Session) -> list[dict]:
    """Return all excluded domains."""
    rows = db.query(ExcludedDomain).order_by(ExcludedDomain.domain).all()
    return [
        {"domain": r.domain, "category": r.category, "notes": r.notes}
        for r in rows
    ]


def add_excluded_domain(
    domain: str, category: str, db: Session, notes: Optional[str] = None
) -> dict:
    """Add a domain to the exclusion list. Idempotent."""
    domain = domain.strip().lower()
    existing = db.query(ExcludedDomain).filter_by(domain=domain).first()
    if existing:
        existing.category = category
        if notes is not None:
            existing.notes = notes
        db.commit()
        return {"domain": domain, "action": "updated"}

    db.add(ExcludedDomain(domain=domain, category=category, notes=notes))
    db.commit()
    return {"domain": domain, "action": "added"}


def remove_excluded_domain(domain: str, db: Session) -> bool:
    """Remove a domain from the exclusion list. Returns True if found."""
    domain = domain.strip().lower()
    row = db.query(ExcludedDomain).filter_by(domain=domain).first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


# ── Combined suppression check ───────────────────────────────────────────────


def _load_suppressed_emails(db: Session, user_id: Optional[str]) -> set[str]:
    """Return the set of lowercased emails suppressed for this user.

    Global (NULL-user) entries always apply. User-scoped entries only
    apply when they match the caller's user_id. Running this once per
    batch instead of per-prospect turns the N+1 (deep audit MEDIUM
    finding) into a single query.
    """
    q = db.query(UnsubscribeEntry.email, UnsubscribeEntry.user_id)
    if user_id is None:
        # Caller has no user context  -  treat every entry as in scope
        # (matches legacy single-rep behaviour).
        rows = q.all()
    else:
        rows = q.filter(
            or_(UnsubscribeEntry.user_id.is_(None), UnsubscribeEntry.user_id == user_id)
        ).all()
    return {(r.email or "").strip().lower() for r in rows if r.email}


def _load_excluded_domains(db: Session) -> dict[str, str]:
    """Return {domain: category} for every excluded domain. Single query."""
    rows = db.query(ExcludedDomain.domain, ExcludedDomain.category).all()
    return {(r.domain or "").strip().lower(): (r.category or "excluded") for r in rows}


def is_prospect_blocked(
    email: str,
    db: Session,
    user_id: Optional[str] = None,
    suppressed_emails: Optional[set[str]] = None,
    excluded_domains: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """
    Check if a prospect should be blocked from outreach.
    Returns the reason string if blocked, None if clear.

    P3-5: accepts an optional pre-loaded ``suppressed_emails`` set and
    ``excluded_domains`` dict so a batch caller can share one DB read
    across many prospects (replaces the per-prospect N+1 query). When
    called stand-alone, the function falls back to querying the DB,
    scoped to ``user_id`` if provided.
    """
    email_lower = email.strip().lower()

    if suppressed_emails is None:
        suppressed_emails = _load_suppressed_emails(db, user_id)
    if excluded_domains is None:
        excluded_domains = _load_excluded_domains(db)

    if email_lower in suppressed_emails:
        return "unsubscribed"

    if "@" in email_lower:
        domain = email_lower.split("@", 1)[1]
        category = excluded_domains.get(domain)
        if category is not None:
            return f"domain_excluded:{category}"

    return None


def filter_prospects_for_campaign(
    prospect_ids: list[str], db: Session, user_id: Optional[str] = None,
) -> tuple[list[str], list[dict]]:
    """
    Filter a list of prospect IDs, removing any that are suppressed or domain-excluded.

    P3-5: loads the suppression set and excluded-domain map ONCE at the
    top of the function rather than per-prospect. Scoped to ``user_id``
    when provided; when None, every suppression entry is considered in
    scope (legacy behaviour).

    Returns:
        (allowed_ids, blocked_list)
        blocked_list: [{"prospect_id": str, "email": str, "reason": str}, ...]
    """
    allowed = []
    blocked = []

    # Preload the per-user suppression set + domain map once.
    suppressed_emails = _load_suppressed_emails(db, user_id)
    excluded_domains = _load_excluded_domains(db)

    # Chunk queries to stay within SQLite's 999 variable limit for IN clauses
    CHUNK = 500
    prospects = []
    for i in range(0, len(prospect_ids), CHUNK):
        chunk = prospect_ids[i:i + CHUNK]
        prospects.extend(db.query(Prospect).filter(Prospect.id.in_(chunk)).all())

    for p in prospects:
        reason = is_prospect_blocked(
            p.email, db,
            user_id=user_id,
            suppressed_emails=suppressed_emails,
            excluded_domains=excluded_domains,
        )
        if reason:
            blocked.append({"prospect_id": p.id, "email": p.email, "reason": reason})
        else:
            allowed.append(p.id)

    if blocked:
        logger.info(
            "Pre-launch filter: %d prospects blocked (%d allowed)",
            len(blocked), len(allowed),
        )

    return allowed, blocked
