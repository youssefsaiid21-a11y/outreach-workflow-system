import uuid
import logging
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db.database import get_db
from ..db.models import StyleProfile, User
from ..services.outlook import OutlookClient
from ..services.style_analyzer import analyze_style, build_style_prompt
from .auth import get_current_user

router = APIRouter(prefix="/style", tags=["style"])
logger = logging.getLogger(__name__)


def _user_token_cache(current_user: "User | None") -> Path:
    """Per-user token cache file for the Style sync flow.

    **Split-brain fix (2026-04-09):** aligned with the OAuth device-flow
    writer in ``src/api/outlook_auth.py``. Previously this returned
    ``tokens/outlook_{user_id}.json`` (a path that never existed on prod
    because nothing wrote to it), causing style sync to kick off a fresh
    device-code flow even for users who had already connected via the
    dashboard "Connect Outlook" button. Legacy callers who did have a
    file at the old path are still honoured via a fallback.
    """
    base = Path(__file__).parent.parent.parent  # outreach-system/
    if current_user:
        canonical = base / f"token_cache_outlook_{current_user.id}.json"
        if canonical.exists():
            return canonical
        legacy = base / "tokens" / f"outlook_{current_user.id}.json"
        if legacy.exists():
            return legacy
        return canonical
    return base / "token_cache.json"


@router.post("/sync")
def sync_style(
    email_count: int = 200,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Authenticates with Outlook and fetches the last N sent emails.
    Analyzes writing style and saves the profile to the database.

    FIX-7: scoped to the calling user  -  sync no longer wipes every user's
    profile, and uses a per-user Outlook token cache.
    """
    outlook = OutlookClient(token_file=_user_token_cache(current_user))
    try:
        outlook.authenticate()
        emails = outlook.fetch_sent_emails(count=email_count)
    finally:
        outlook.close()

    if not emails:
        raise HTTPException(status_code=400, detail="No sent emails found in Outlook.")

    profile_data = analyze_style(emails)

    try:
        # Only delete THIS user's existing profile rows.
        del_q = db.query(StyleProfile)
        if current_user:
            del_q = del_q.filter(StyleProfile.user_id == current_user.id)
        else:
            # Legacy first-run: no users  -  only touch the unowned row.
            del_q = del_q.filter(StyleProfile.user_id.is_(None))
        del_q.delete(synchronize_session=False)

        profile = StyleProfile(
            id=str(uuid.uuid4()),
            user_id=current_user.id if current_user else None,
            avg_word_count=profile_data.get("avg_word_count"),
            formality=profile_data.get("formality"),
            greeting_patterns=profile_data.get("greeting_patterns", []),
            sign_off_patterns=profile_data.get("sign_off_patterns", []),
            avg_sentence_length=profile_data.get("avg_sentence_length"),
            uses_bullet_points=profile_data.get("uses_bullet_points", False),
            bullet_point_frequency=profile_data.get("bullet_point_frequency", 0.0),
            sample_emails=profile_data.get("sample_emails", []),
            raw_profile=profile_data,
        )
        db.add(profile)
        db.commit()
    except Exception:
        db.rollback()
        raise

    logger.info(f"Style profile saved. Analyzed {profile_data.get('total_emails_analyzed', 0)} emails.")
    return {
        "synced": True,
        "emails_analyzed": profile_data.get("total_emails_analyzed", 0),
        "profile": _profile_to_dict(profile),
    }


@router.get("/")
def get_style_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = _get_latest_profile_model(db, current_user)
    if not profile:
        return {"profile": None, "message": "No style profile found. Run /style/sync first."}
    return {"profile": _profile_to_dict(profile)}


@router.get("/preview-prompt")
def get_style_prompt(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the style context block that gets injected into email generation prompts.
    Useful for debugging what the AI sees.
    """
    profile = _get_latest_profile(db, current_user)
    if not profile:
        return {"prompt": "No style profile found."}
    return {"prompt": build_style_prompt(profile)}


def _get_latest_profile(db: Session, current_user: "User | None" = None) -> Optional[dict]:
    profile = _get_latest_profile_model(db, current_user)
    if profile:
        return profile.raw_profile
    return None


def _get_latest_profile_model(db: Session, current_user: "User | None" = None) -> Optional[StyleProfile]:
    """Return the latest StyleProfile for the current user.

    P3-6: NEVER falls back to another user's profile. If the current
    user has no profile, returns None so the caller can render the
    neutral default prompt. Anonymous callers (no current_user  -  only
    possible on first-run legacy paths) still receive the unowned
    profile so the single-rep baseline keeps working.
    """
    q = db.query(StyleProfile)
    if current_user is not None:
        return (
            q.filter(StyleProfile.user_id == current_user.id)
            .order_by(StyleProfile.synced_at.desc())
            .first()
        )
    # Legacy / unauthenticated path only: return the unowned profile if any.
    return (
        q.filter(StyleProfile.user_id.is_(None))
        .order_by(StyleProfile.synced_at.desc())
        .first()
    )


def _profile_to_dict(p: StyleProfile) -> dict:
    return {
        "id": p.id,
        "synced_at": p.synced_at.isoformat() if p.synced_at else None,
        "avg_word_count": p.avg_word_count,
        "avg_sentence_length": p.avg_sentence_length,
        "formality": p.formality,
        "greeting_patterns": p.greeting_patterns or [],
        "sign_off_patterns": p.sign_off_patterns or [],
        "uses_bullet_points": p.uses_bullet_points,
        "bullet_point_frequency": p.bullet_point_frequency,
        "sample_emails": p.sample_emails or [],
    }
