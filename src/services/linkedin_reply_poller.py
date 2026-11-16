"""
LinkedIn reply detection -- polls PB Inbox Scraper results to detect
when prospects reply to our LinkedIn messages.

Runs every 30 minutes as a background daemon thread. On each cycle:
1. Fetches the Inbox Scraper's cumulative result.json from PB S3
2. Filters for conversations where isLastMessageFromMe == False (they replied)
3. Normalises LinkedIn URLs and matches against prospects in our DB
4. For matched prospects with status 'messaged': updates to 'replied'
5. Also updates the EmailSequence status to 'replied'
6. Logs each new reply detection

Public API:
  - start_linkedin_reply_poller(): called from main.py lifespan
  - stop_linkedin_reply_poller(): called from main.py shutdown
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# -- Module state (single poller per process) ---------------------------------
_poller_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()

# Dedup: track thread URLs already processed in this process lifetime.
# Reset on restart is fine -- the status check makes re-processing idempotent.
_processed_threads: set[str] = set()

# Per-agent cached S3 folder info so we don't call PB /agents/fetch every cycle.
_s3_cache: dict[str, dict] = {}  # agent_id -> {"orgS3Folder": ..., "s3Folder": ...}

# Poll interval (seconds). Default 5 min — fast enough to catch replies
# before Youssef responds (which flips isLastMessageFromMe to True).
LINKEDIN_REPLY_POLL_INTERVAL = int(
    os.getenv("LINKEDIN_REPLY_POLL_SECONDS", "300")
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# -- Public lifecycle API -----------------------------------------------------


def linkedin_reply_poller_alive() -> bool:
    """Return True if the poller thread is currently running."""
    return _poller_thread is not None and _poller_thread.is_alive()


def start_linkedin_reply_poller() -> None:
    """Start the LinkedIn reply poller thread.

    Idempotent: a second call while a poller is alive is a no-op. The
    poller is a daemon thread so the process can exit cleanly even if
    the lifespan shutdown handler is skipped.
    """
    global _poller_thread
    if linkedin_reply_poller_alive():
        logger.debug("linkedin_reply_poller: already running, ignoring start request")
        return

    _stop_event.clear()
    _poller_thread = threading.Thread(
        target=_poller_loop, name="li-reply-poller", daemon=True
    )
    _poller_thread.start()
    logger.info(
        "linkedin_reply_poller: started (interval=%ds)",
        LINKEDIN_REPLY_POLL_INTERVAL,
    )


def stop_linkedin_reply_poller(timeout: float = 30.0) -> None:
    """Signal the poller to stop and wait up to ``timeout`` seconds for it."""
    global _poller_thread
    if not linkedin_reply_poller_alive():
        return
    _stop_event.set()
    logger.info("linkedin_reply_poller: stop signal sent")
    _poller_thread.join(timeout=timeout)
    if _poller_thread.is_alive():
        logger.warning(
            "linkedin_reply_poller: did not drain within %.0fs -- exiting anyway",
            timeout,
        )
    else:
        logger.info("linkedin_reply_poller: drained cleanly")
    _poller_thread = None


# -- Poller loop --------------------------------------------------------------


def _poller_loop() -> None:
    """Main loop: iterate all LinkedIn accounts with inbox scrapers, match replies."""
    from ..db.database import SessionLocal

    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            try:
                _poll_all_accounts(db)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "linkedin_reply_poller: top-level crash: %s",
                    exc, exc_info=True,
                )
                try:
                    db.rollback()
                except Exception:
                    pass
        finally:
            try:
                db.close()
            except Exception:
                pass

        if _stop_event.wait(timeout=LINKEDIN_REPLY_POLL_INTERVAL):
            return


def _poll_all_accounts(db) -> int:
    """Iterate all LinkedIn accounts with inbox_scraper_agent_id and poll each.

    Falls back to the legacy single-agent approach if no accounts are configured.
    Returns total replies detected across all accounts.
    """
    from ..db.models import LinkedInAccount

    accounts = (
        db.query(LinkedInAccount)
        .filter(
            LinkedInAccount.inbox_scraper_agent_id.isnot(None),
            LinkedInAccount.is_active == True,
        )
        .all()
    )

    if not accounts:
        # Legacy fallback: use env var agent ID
        logger.debug("linkedin_reply_poller: no accounts with inbox scraper, using legacy")
        return _poll_inbox_scraper(db)

    total = 0
    for account in accounts:
        if _stop_event.is_set():
            break
        try:
            count = _poll_inbox_scraper(db, inbox_agent_id=account.inbox_scraper_agent_id,
                                         account_name=account.name)
            total += count
        except Exception as exc:
            logger.error(
                "linkedin_reply_poller: failed for account '%s': %s",
                account.name, exc,
            )
            try:
                db.rollback()
            except Exception:
                pass

    return total


# -- Core logic ---------------------------------------------------------------


def _get_inbox_scraper_agent_id() -> str:
    """Return the PB Inbox Scraper agent ID from env or default."""
    return os.getenv("PHANTOMBUSTER_INBOX_SCRAPER_AGENT_ID", "5271450334692625")


def _get_s3_info(pb_client, agent_id: str = None) -> dict:
    """Fetch and cache the agent's S3 folder info from PB API.

    Args:
        pb_client: PhantomBuster API client.
        agent_id: PB agent ID. Defaults to env var if not provided.

    Returns dict with keys 'orgS3Folder' and 's3Folder'.
    Caches per agent_id so subsequent cycles skip the /agents/fetch call.
    """
    if agent_id is None:
        agent_id = _get_inbox_scraper_agent_id()

    if agent_id in _s3_cache:
        return _s3_cache[agent_id]

    agent_data = pb_client.get_agent(agent_id)

    org_s3 = agent_data.get("orgS3Folder", "")
    agent_s3 = agent_data.get("s3Folder", "")
    if not org_s3 or not agent_s3:
        raise ValueError(
            f"PB agent {agent_id} missing s3Folder info: "
            f"orgS3Folder={org_s3!r}, s3Folder={agent_s3!r}"
        )

    _s3_cache[agent_id] = {"orgS3Folder": org_s3, "s3Folder": agent_s3}
    logger.info(
        "linkedin_reply_poller: cached S3 info for agent %s: org=%s agent=%s",
        agent_id, org_s3, agent_s3,
    )
    return _s3_cache[agent_id]


def _fetch_result_json(pb_client, agent_id: str = None) -> list[dict]:
    """Download the Inbox Scraper's cumulative result.json from PB S3.

    Args:
        pb_client: PhantomBuster API client.
        agent_id: PB agent ID. Defaults to env var if not provided.

    Returns a list of conversation thread dicts. Returns an empty list
    if the download fails (logged as warning, retried next cycle).
    """
    s3_info = _get_s3_info(pb_client, agent_id=agent_id)
    url = (
        f"https://phantombuster.s3.amazonaws.com/"
        f"{s3_info['orgS3Folder']}/"
        f"{s3_info['s3Folder']}/result.json"
    )

    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        # result.json can be a list or a single object
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return data
        logger.warning("linkedin_reply_poller: unexpected result.json type: %s", type(data))
        return []
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "linkedin_reply_poller: S3 download failed (%s %s): %s",
            exc.response.status_code, url, exc,
        )
        return []
    except Exception as exc:
        logger.warning(
            "linkedin_reply_poller: S3 download error: %s", exc,
        )
        return []


def _poll_inbox_scraper(db, inbox_agent_id: str = None, account_name: str = None) -> int:
    """One poll cycle: fetch results for one inbox scraper, match replies, update DB.

    Args:
        db: SQLAlchemy session.
        inbox_agent_id: PB agent ID for the inbox scraper. If None, uses env var default.
        account_name: Display name for logging (e.g. "Jun Hao").

    Returns the number of prospects newly marked as replied.
    """
    from .phantombuster import PhantomBusterClient
    from .linkedin_batch import normalize_linkedin_url
    from ..db.models import (
        Prospect, ProspectStatus,
        EmailSequence, SequenceStatus,
        Campaign,
    )

    label = f"account '{account_name}'" if account_name else "legacy"
    pb = PhantomBusterClient()
    rows = _fetch_result_json(pb, agent_id=inbox_agent_id)
    if not rows:
        logger.debug("linkedin_reply_poller [%s]: no results from Inbox Scraper", label)
        return 0

    marked = 0
    for row in rows:
        # Only reliable signal: isLastMessageFromMe=False (prospect sent last).
        # We CANNOT detect replies when Youssef already replied back (flips to True)
        # because we can't distinguish "reply to their response" from "second outreach".
        # This gives us a lower bound on replies, but with 100% accuracy.
        if row.get("isLastMessageFromMe") is not False:
            continue

        # Dedup by thread URL
        thread_url = row.get("threadUrl", "")
        if not thread_url:
            continue
        if thread_url in _processed_threads:
            continue

        # Extract the other person's LinkedIn URL
        linked_in_urls = row.get("linkedInUrls")
        if not linked_in_urls or not isinstance(linked_in_urls, list):
            continue
        raw_url = linked_in_urls[0]
        if not raw_url:
            continue

        try:
            normalised_url = normalize_linkedin_url(raw_url)
        except Exception:
            logger.debug("linkedin_reply_poller: could not normalise URL %s", raw_url)
            continue

        # Find matching prospect in 'messaged' status.
        # Strategy 1: match by normalised LinkedIn URL
        # Strategy 2: fall back to name matching (PB Inbox Scraper returns
        #   opaque entity URNs like /in/ACoAADsh... instead of /in/john-smith,
        #   so URL matching often fails — name matching catches those cases)
        candidates = (
            db.query(Prospect)
            .filter(
                Prospect.status == ProspectStatus.messaged,
            )
            .all()
        )
        matched_prospect = None

        # Try URL match first (most precise)
        if normalised_url:
            for p in candidates:
                try:
                    if p.linkedin_url and normalize_linkedin_url(p.linkedin_url) == normalised_url:
                        matched_prospect = p
                        break
                except Exception:
                    continue

        # Fall back to name match if URL didn't work.
        # Since we only process isLastMessageFromMe=False, firstnameFrom IS the prospect.
        if not matched_prospect:
            reply_first = (row.get("firstnameFrom") or "").strip().lower()
            reply_last = (row.get("lastnameFrom") or "").strip().lower()
            if reply_first:
                for p in candidates:
                    p_first = (p.first_name or "").strip().lower()
                    p_last = (p.last_name or "").strip().lower()
                    if p_first and p_first == reply_first and p_last == reply_last:
                        matched_prospect = p
                        break

        if not matched_prospect:
            _processed_threads.add(thread_url)
            continue


        # Parse reply timestamp
        last_msg_date_str = row.get("lastMessageDate", "")
        reply_at = _parse_timestamp(last_msg_date_str) or _utcnow()
        reply_name = " ".join(
            filter(None, [row.get("firstnameFrom", ""), row.get("lastnameFrom", "")])
        )

        # Update prospect status to replied
        matched_prospect.status = ProspectStatus.replied
        logger.info(
            "LinkedIn reply detected: %s (%s) replied -- prospect %s",
            reply_name, raw_url, matched_prospect.id,
        )

        # Update matching EmailSequence rows for this prospect
        sequences = (
            db.query(EmailSequence)
            .filter(
                EmailSequence.prospect_id == matched_prospect.id,
                EmailSequence.status == SequenceStatus.messaged,
            )
            .all()
        )
        for seq in sequences:
            seq.status = SequenceStatus.replied
            seq.replied_at = reply_at.replace(tzinfo=None) if reply_at.tzinfo else reply_at
            # Log with campaign name for clarity
            try:
                camp = db.query(Campaign).filter(Campaign.id == seq.campaign_id).first()
                camp_name = camp.name if camp else seq.campaign_id
            except Exception:
                camp_name = seq.campaign_id
            logger.info(
                "LinkedIn reply detected: %s replied to campaign '%s'",
                reply_name or matched_prospect.first_name, camp_name,
            )

        marked += 1
        _processed_threads.add(thread_url)

    if marked:
        db.commit()
        logger.info("linkedin_reply_poller [%s]: marked %d prospect(s) as replied", label, marked)

    return marked


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 or common date string from PB results.

    PB timestamps can be ISO 8601 or other formats. We try fromisoformat
    first, then fall back to common patterns.
    """
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        pass
    # Try common PB format: "2026-06-09 14:30:00"
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass
    return None
