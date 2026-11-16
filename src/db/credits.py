"""
Lightweight credit tracking. Call log_credits() whenever an API operation
consumes credits (Apollo reveals, Reacher validations, Proxycurl lookups).
"""
import uuid
import logging
from datetime import datetime, timezone
from .database import SessionLocal
from .models import CreditLog

logger = logging.getLogger(__name__)


def log_credits(provider: str, operation: str, credits: int = 1, context: str = None, user_id: str = None):
    """
    Records API credit consumption to the credit_logs table.
    Silently skips on any DB error  -  credit logging must never break the main flow.
    """
    db = SessionLocal()
    try:
        db.add(CreditLog(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider=provider,
            operation=operation,
            credits_used=credits,
            context=context,
        ))
        db.commit()
    except Exception as e:
        logger.warning("Credit log write failed: %s", e)
    finally:
        db.close()


def get_summary(days: int = 30, user_id: str = None) -> dict:
    """Returns credit usage totals per provider for the last N days, optionally filtered by user."""
    from datetime import timedelta
    from sqlalchemy import func
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        q = (
            db.query(CreditLog.provider, func.sum(CreditLog.credits_used))
            .filter(CreditLog.created_at >= cutoff)
        )
        if user_id:
            q = q.filter(CreditLog.user_id == user_id)
        rows = q.group_by(CreditLog.provider).all()
        return {provider: int(total) for provider, total in rows}
    finally:
        db.close()


def get_summary_by_user(days: int = 30) -> dict:
    """Returns credit usage per user per provider for the last N days."""
    from datetime import timedelta
    from sqlalchemy import func
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        rows = (
            db.query(CreditLog.user_id, CreditLog.provider, func.sum(CreditLog.credits_used))
            .filter(CreditLog.created_at >= cutoff)
            .group_by(CreditLog.user_id, CreditLog.provider)
            .all()
        )
        result = {}
        for uid, provider, total in rows:
            uid = uid or "unknown"
            if uid not in result:
                result[uid] = {}
            result[uid][provider] = int(total)
        return result
    finally:
        db.close()
