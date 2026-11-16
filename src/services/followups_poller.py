"""
Follow-ups poller — monitors HubSpot deals and creates Trello cards for overdue follow-ups.

Part of the GTM analytics / follow-ups machine. This background daemon:

  1. **Syncs deals from HubSpot**: Fetches all deals in configured pipeline stages,
     enriches them with owner/contact/company info, and upserts into the FollowupDeal
     table. Runs every 4 hours by default (FOLLOWUPS_POLLER_INTERVAL_SECONDS).

  2. **Detects overdue deals**: Any deal where the last real activity (logged call,
     email, meeting, or note) is older than FOLLOWUPS_STALE_DAYS (default 7) is
     flagged as overdue. NOTE: hs_lastmodifieddate is deliberately excluded from
     activity detection — it updates on ANY property change including automations,
     which creates false "just contacted" signals.

  3. **Creates Trello cards**: For overdue deals that don't already have a Trello card,
     creates one in the configured Trello list with deal details, HubSpot link, and
     auto-assigned to the deal owner (matched by name/username). Includes dedup logic
     to handle cards created manually in Trello.

  4. **AI next-step suggestions**: Uses Claude Haiku to generate a short (max 10 words)
     suggested next action for overdue deals, based on the deal context and recent
     HubSpot notes. Batched to 20 deals per scan to control API costs.

Configuration (stored in AppSettings table):
  - followups_active_stage_ids: JSON array of HubSpot stage IDs to monitor
  - followups_trello_board_id: Trello board for card creation
  - followups_trello_list_id: Trello list for new cards

Public API:
  - start_followups_poller() / stop_followups_poller() — lifecycle management
  - followups_poller_alive() — watchdog liveness check
  - poll_deals(db) — synchronous scan, also callable from the "Scan Now" endpoint
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import FollowupDeal, AppSetting

logger = logging.getLogger(__name__)

# ── Module state ──────────────────────────────────────────────────────────
_poller_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()

POLLER_INTERVAL_SECONDS = int(
    os.getenv("FOLLOWUPS_POLLER_INTERVAL_SECONDS", "14400")  # 4 hours
)
STALE_DAYS = int(os.getenv("FOLLOWUPS_STALE_DAYS", "7"))

# AppSettings keys
KEY_ACTIVE_STAGES = "followups_active_stage_ids"
KEY_TRELLO_BOARD = "followups_trello_board_id"
KEY_TRELLO_LIST = "followups_trello_list_id"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Public lifecycle ──────────────────────────────────────────────────────

def followups_poller_alive() -> bool:
    return _poller_thread is not None and _poller_thread.is_alive()


def start_followups_poller() -> None:
    global _poller_thread
    if followups_poller_alive():
        return
    _stop_event.clear()
    _poller_thread = threading.Thread(
        target=_poller_loop, name="followups-poller", daemon=True
    )
    _poller_thread.start()
    logger.info("followups_poller: started (interval=%ds, stale_days=%d)",
                POLLER_INTERVAL_SECONDS, STALE_DAYS)


def stop_followups_poller(timeout: float = 15.0) -> None:
    global _poller_thread
    if not followups_poller_alive():
        return
    _stop_event.set()
    logger.info("followups_poller: stop signal sent")
    _poller_thread.join(timeout=timeout)
    if _poller_thread.is_alive():
        logger.warning("followups_poller: did not drain within %.0fs", timeout)
    else:
        logger.info("followups_poller: drained cleanly")
    _poller_thread = None


# ── Poller loop ───────────────────────────────────────────────────────────

def _poller_loop() -> None:
    from ..db.database import SessionLocal

    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            try:
                poll_deals(db)
            except Exception as exc:  # noqa: BLE001
                logger.error("followups_poller: crash: %s", exc, exc_info=True)
                try:
                    db.rollback()
                except Exception:
                    pass
        finally:
            try:
                db.close()
            except Exception:
                pass

        if _stop_event.wait(timeout=POLLER_INTERVAL_SECONDS):
            return


# ── Core poll logic (also called by manual scan endpoint) ─────────────────

def poll_deals(db: Session) -> dict:
    """Sync deals from HubSpot, detect overdue, create Trello cards.

    Returns summary: {deals_synced, newly_overdue, cards_created}
    """
    # Read config
    config = _read_config(db)
    if not config["active_stage_ids"]:
        logger.info("followups_poller: no active stages configured, skipping")
        return {"deals_synced": 0, "newly_overdue": 0, "cards_created": 0}

    # Fetch deals from HubSpot
    from .hubspot import HubSpotClient
    try:
        hs = HubSpotClient()
    except ValueError as exc:
        logger.warning("followups_poller: %s", exc)
        return {"deals_synced": 0, "newly_overdue": 0, "cards_created": 0}

    try:
        # Get stage label map for display
        stage_labels = {}
        try:
            for pipeline in hs.list_pipelines():
                for stage in pipeline["stages"]:
                    stage_labels[stage["id"]] = stage["label"]
        except Exception as exc:
            logger.warning("followups_poller: could not load stage labels: %s", exc)

        deals = hs.list_deals(stage_ids=config["active_stage_ids"])

        # Batch-load all owners (one API call instead of per-deal)
        owner_cache: dict[str, dict] = hs.list_owners()
        now = _utcnow()

        deals_synced = 0
        newly_overdue = 0

        for deal in deals:
            try:
                # Enrich owner (from batch cache, fallback to per-ID lookup)
                owner_id = deal.get("hubspot_owner_id")
                owner = {"name": "", "email": ""}
                if owner_id:
                    owner = owner_cache.get(str(owner_id), owner_cache.get(owner_id, {}))
                    if not owner.get("name") and not owner.get("email"):
                        owner = hs.get_owner(str(owner_id))

                # Enrich contact (first one)
                contact = {"name": "", "email": ""}
                if deal.get("contact_ids"):
                    contact = hs.get_contact(deal["contact_ids"][0])

                # Enrich company (first one)
                company = {"name": ""}
                if deal.get("company_ids"):
                    company = hs.get_company(deal["company_ids"][0])

                # Parse last activity
                last_activity = _parse_datetime(deal.get("last_activity_at"))
                days_since = (now - last_activity).days if last_activity else 999
                is_overdue = days_since >= STALE_DAYS

                stage_label = stage_labels.get(deal["deal_stage_id"], deal["deal_stage_id"])

                # Upsert into DB
                existing = db.query(FollowupDeal).filter(
                    FollowupDeal.hubspot_deal_id == deal["id"]
                ).first()

                if existing:
                    existing.deal_name = deal["deal_name"]
                    existing.deal_stage = stage_label
                    existing.deal_stage_id = deal["deal_stage_id"]
                    existing.pipeline_id = deal["pipeline_id"]
                    existing.amount = deal["amount"]
                    existing.owner_name = owner["name"]
                    existing.owner_email = owner["email"]
                    existing.hubspot_owner_id = owner_id
                    existing.company_name = company["name"]
                    existing.contact_name = contact["name"]
                    existing.contact_email = contact["email"]
                    existing.last_activity_at = last_activity
                    existing.last_synced_at = now
                    existing.days_since_activity = days_since
                    was_overdue = existing.is_overdue
                    existing.is_overdue = is_overdue
                    existing.updated_at = now
                    if is_overdue and not was_overdue:
                        newly_overdue += 1
                else:
                    row = FollowupDeal(
                        id=str(uuid.uuid4()),
                        hubspot_deal_id=deal["id"],
                        deal_name=deal["deal_name"],
                        deal_stage=stage_label,
                        deal_stage_id=deal["deal_stage_id"],
                        pipeline_id=deal["pipeline_id"],
                        amount=deal["amount"],
                        owner_name=owner["name"],
                        owner_email=owner["email"],
                        hubspot_owner_id=owner_id,
                        company_name=company["name"],
                        contact_name=contact["name"],
                        contact_email=contact["email"],
                        last_activity_at=last_activity,
                        last_synced_at=now,
                        days_since_activity=days_since,
                        is_overdue=is_overdue,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(row)
                    if is_overdue:
                        newly_overdue += 1

                deals_synced += 1
                # Flush per deal so one failure doesn't roll back the entire batch
                if deals_synced % 10 == 0:
                    db.flush()
            except Exception as exc:
                logger.warning("followups_poller: failed to sync deal %s: %s",
                               deal.get("deal_name", deal.get("id", "?")), exc)
                try:
                    db.rollback()
                except Exception:
                    pass

        db.commit()

        # Generate suggested next steps for overdue deals without one
        _generate_next_steps(db, hs)

    finally:
        hs.close()

    # Create Trello cards for overdue deals
    cards_created = 0
    if config["trello_list_id"]:
        cards_created = _create_trello_cards(db, config)

    logger.info(
        "followups_poller: synced=%d, newly_overdue=%d, cards_created=%d",
        deals_synced, newly_overdue, cards_created,
    )
    return {"deals_synced": deals_synced, "newly_overdue": newly_overdue, "cards_created": cards_created}


def _create_trello_cards(db: Session, config: dict) -> int:
    """Create Trello cards for overdue deals that don't have one yet.

    Each card includes: deal name, company, contact, stage, amount, owner,
    last activity date, days since activity, and a HubSpot deep link.
    Cards are auto-assigned to the deal owner (matched by Trello username/name).
    Due date is set to today (needs immediate follow-up).

    Dedup: Before creating, checks if a card with the same name already exists
    in the target list. If so, backfills the trello_card_id on the deal row
    without creating a duplicate.

    Snoozed deals (snoozed_until > now) are skipped even if overdue.
    """
    from .trello import TrelloClient
    try:
        trello = TrelloClient()
    except ValueError as exc:
        logger.warning("followups_poller: Trello not configured: %s", exc)
        return 0

    now = _utcnow()
    list_id = config["trello_list_id"]
    board_id = config["trello_board_id"]

    # Get board members for assignment
    members = []
    try:
        members = trello.list_board_members(board_id)
    except Exception as exc:
        logger.warning("followups_poller: could not load Trello members: %s", exc)

    # Build email → member_id map (lowercase)
    email_to_member = {}
    for m in members:
        # Trello doesn't expose email via API, so we match on username
        # Store both name and username for flexible matching
        email_to_member[m.get("username", "").lower()] = m["id"]
        email_to_member[m.get("name", "").lower()] = m["id"]

    # Find overdue deals without Trello cards (and not snoozed)
    overdue = db.query(FollowupDeal).filter(
        FollowupDeal.is_overdue == True,
        FollowupDeal.trello_card_id.is_(None),
    ).all()

    cards_created = 0
    for deal in overdue:
        # Check snooze
        if deal.snoozed_until and deal.snoozed_until.replace(tzinfo=timezone.utc) > now:
            continue

        card_name = f"Follow up: {deal.deal_name}"

        # Dedup: check if card already exists in Trello
        existing_card = trello.find_card_by_name(list_id, card_name)
        if existing_card:
            deal.trello_card_id = existing_card["id"]
            deal.trello_card_url = existing_card["url"]
            deal.trello_card_created_at = now
            deal.updated_at = now
            continue

        # Build description
        lines = [
            f"**Deal:** {deal.deal_name}",
            f"**Company:** {deal.company_name or 'N/A'}",
            f"**Contact:** {deal.contact_name or 'N/A'} ({deal.contact_email or 'N/A'})",
            f"**Stage:** {deal.deal_stage or 'N/A'}",
            f"**Amount:** ${deal.amount:,.0f}" if deal.amount else "**Amount:** N/A",
            f"**Owner:** {deal.owner_name or 'N/A'} ({deal.owner_email or 'N/A'})",
            f"**Last Activity:** {deal.last_activity_at.strftime('%Y-%m-%d') if deal.last_activity_at else 'Never'}",
            f"**Days Since Activity:** {deal.days_since_activity}",
            f"",
            f"[View in HubSpot](https://app.hubspot.com/contacts/deals/{deal.hubspot_deal_id})",
        ]
        desc = "\n".join(lines)

        # Match owner to Trello member
        member_ids = []
        if deal.owner_email:
            owner_lower = deal.owner_email.lower()
            # Try exact email match, then name parts
            for key, mid in email_to_member.items():
                if owner_lower == key or deal.owner_name and deal.owner_name.lower() == key:
                    member_ids.append(mid)
                    break

        # Due date = today (needs immediate follow-up)
        due = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        try:
            card = trello.create_card(
                list_id=list_id,
                name=card_name,
                desc=desc,
                due=due,
                member_ids=member_ids if member_ids else None,
            )
            deal.trello_card_id = card["id"]
            deal.trello_card_url = card["url"]
            deal.trello_card_created_at = now
            deal.updated_at = now
            cards_created += 1
        except Exception as exc:
            logger.warning("followups_poller: failed to create card for %s: %s",
                           deal.deal_name, exc)

    # Always commit — even if no new cards were created, dedup may have
    # backfilled trello_card_id on existing deals that we found in Trello.
    db.commit()

    try:
        trello.close()
    except Exception:
        pass

    return cards_created


# ── AI next-step generation ───────────────────────────────────────────────

def _generate_next_steps(db: Session, hs) -> None:
    """Generate short AI-suggested next steps for overdue deals missing one.

    Uses Claude Haiku (cheapest/fastest model) to produce a max-10-word
    actionable suggestion like "Check in on proposal status" or "Schedule
    demo follow-up". Feeds the deal context + recent HubSpot notes into
    the prompt. Limited to 20 deals per scan to control API costs.
    """
    deals_needing = db.query(FollowupDeal).filter(
        FollowupDeal.is_overdue == True,
        (FollowupDeal.suggested_next_step.is_(None)) | (FollowupDeal.suggested_next_step == ""),
    ).limit(20).all()  # batch of 20 max per scan to control API costs

    if not deals_needing:
        return

    try:
        import anthropic
        client = anthropic.Anthropic(timeout=15.0)
    except Exception as exc:
        logger.warning("followups_poller: cannot init Anthropic for next steps: %s", exc)
        return

    for deal in deals_needing:
        try:
            # Fetch notes from HubSpot
            notes = hs.get_deal_notes(deal.hubspot_deal_id, limit=3)
            notes_text = "\n".join(notes) if notes else "No notes available."

            prompt = (
                f"Deal: {deal.deal_name}\n"
                f"Company: {deal.company_name or 'Unknown'}\n"
                f"Stage: {deal.deal_stage or 'Unknown'}\n"
                f"Days since last activity: {deal.days_since_activity}\n"
                f"Contact: {deal.contact_name or 'Unknown'}\n"
                f"Recent notes:\n{notes_text}\n\n"
                f"Write a single short sentence (max 10 words) suggesting the specific next follow-up action. "
                f"Be direct and actionable. Examples: 'Check in on proposal status', 'Schedule demo follow-up', "
                f"'Send revised pricing'. No quotes, no explanation."
            )

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=50,
                messages=[{"role": "user", "content": prompt}],
            )
            suggestion = response.content[0].text.strip()
            # Truncate to keep it concise
            deal.suggested_next_step = suggestion[:100]
            deal.updated_at = _utcnow()
        except Exception as exc:
            logger.warning(
                "followups_poller: failed to generate next step for %s: %s",
                deal.deal_name, exc,
            )

    db.commit()
    logger.info("followups_poller: generated next steps for %d deals", len(deals_needing))


# ── Config helpers ────────────────────────────────────────────────────────

def _read_config(db: Session) -> dict:
    """Read follow-ups configuration from AppSettings."""
    stage_ids = []
    trello_board = ""
    trello_list = ""

    row = db.query(AppSetting).filter(AppSetting.key == KEY_ACTIVE_STAGES).first()
    if row and row.value:
        try:
            stage_ids = json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            pass

    row = db.query(AppSetting).filter(AppSetting.key == KEY_TRELLO_BOARD).first()
    if row and row.value:
        trello_board = row.value

    row = db.query(AppSetting).filter(AppSetting.key == KEY_TRELLO_LIST).first()
    if row and row.value:
        trello_list = row.value

    return {
        "active_stage_ids": stage_ids,
        "trello_board_id": trello_board,
        "trello_list_id": trello_list,
    }


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime from HubSpot."""
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
