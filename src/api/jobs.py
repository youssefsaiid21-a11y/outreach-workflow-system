"""
Background job runner for long-running operations.

Provides generic job infrastructure: create/poll/cancel background jobs.
Email sequence generation has been removed; use LinkedIn-only workflows.
"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import Job, User
from .auth import get_current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)


def start_generate_job(campaign_id: str, db: Session, user_id: str = None,
                       retry_prospect_ids: list[str] | None = None) -> dict:
    """Email sequence generation has been removed. Returns 410 Gone."""
    raise HTTPException(410, "Email sequence generation is no longer supported. Use LinkedIn-only workflows.")


def _job_for_user(db: Session, job_id: str, current_user: "User | None") -> Job:
    """FIX-15: fetch job and check ownership. 404 on mismatch."""
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
    if current_user is None:
        if db.query(User).count() > 0:
            raise HTTPException(401, "Authentication required.")
        return job
    if job.user_id is None or job.user_id != current_user.id:
        raise HTTPException(404, "Job not found.")
    return job

# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get("/active")
def get_active_job(
    campaign_id: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the active (running/pending) job for a campaign, if any."""
    import json as _json
    recent = db.query(Job).filter(Job.status.in_(["running", "pending"])).order_by(Job.created_at.desc()).limit(10).all()
    for j in recent:
        if current_user and j.user_id and j.user_id != current_user.id:
            continue
        if campaign_id:
            try:
                p = _json.loads(j.payload) if isinstance(j.payload, str) else j.payload
                if not p or p.get("campaign_id") != campaign_id:
                    continue
            except Exception:
                continue
        return _job_to_dict(j)
    return None


@router.get("/{job_id}")
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = _job_for_user(db, job_id, current_user)
    return _job_to_dict(job)


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = _job_for_user(db, job_id, current_user)
    if job.status not in ("pending", "running"):
        raise HTTPException(400, f"Job is already {job.status}.")
    job.status = "cancelled"
    job.progress_detail = "Cancelled by user."
    db.commit()
    return _job_to_dict(job)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _update_job(db: Session, job: Job, *, status: str = None, detail: str = None,
                progress: int = None, error: str = None, result: dict = None):
    if status is not None:
        job.status = status
    if detail is not None:
        job.progress_detail = detail
    if progress is not None:
        job.progress = progress
    if error:
        job.error = error
    if result is not None:
        job.result = result
    job.updated_at = datetime.now(timezone.utc)
    db.commit()


def _job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status,
        "progress": job.progress,
        "progress_detail": job.progress_detail,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
