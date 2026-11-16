"""
API endpoints for domain exclusion list management and suppression sync.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import User
from .auth import require_api_key, get_current_user
from ..services.suppression import (
    add_excluded_domain,
    remove_excluded_domain,
    get_excluded_domains,
)

logger = logging.getLogger(__name__)

# Defense-in-depth: every endpoint on this router requires a valid API key
# even if a future mount forgets `dependencies=_auth`. See deep audit CRITICAL-5.
router = APIRouter(
    prefix="/exclusions",
    tags=["exclusions"],
    dependencies=[Depends(require_api_key)],
)


class DomainExclusionRequest(BaseModel):
    domain: str
    category: str = "competitor"  # competitor | customer | partner | internal
    notes: Optional[str] = None


@router.get("/")
def list_excluded_domains(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all excluded domains.

    Note: the `excluded_domains` table is currently global (no user_id
    column). All authenticated users see the same list. User-scoping is
    deferred to Phase 3 (multi-tenancy cut)."""
    return get_excluded_domains(db)


@router.post("/")
def add_exclusion(
    req: DomainExclusionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a domain to the exclusion list."""
    domain = req.domain.strip().lower()
    if not domain:
        raise HTTPException(400, "Domain is required.")
    valid_categories = {"competitor", "customer", "partner", "internal"}
    if req.category not in valid_categories:
        raise HTTPException(400, f"category must be one of: {', '.join(valid_categories)}")
    actor = current_user.email if current_user else "<legacy>"
    logger.info("Exclusion added by %s: %s (%s)", actor, domain, req.category)
    return add_excluded_domain(domain, req.category, db, req.notes)


@router.delete("/{domain}")
def remove_exclusion(
    domain: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a domain from the exclusion list."""
    if not remove_excluded_domain(domain, db):
        raise HTTPException(404, f"Domain '{domain}' not found in exclusion list.")
    actor = current_user.email if current_user else "<legacy>"
    logger.info("Exclusion removed by %s: %s", actor, domain)
    return {"removed": domain}


@router.post("/sync-suppression")
def sync_suppression(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Suppression sync endpoint (no-op since Instantly removal)."""
    return {"status": "ok", "detail": "No external sync targets configured."}
