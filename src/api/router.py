"""
FastAPI router registration — maps all API endpoints under /api/*.

This is the central wiring file that connects every feature module to the
FastAPI application. All routes are prefixed with /api/ and (with a few
exceptions) require authentication via the `require_api_key` dependency.

Authentication layers:
  - _auth dependency: applied to most routers via `dependencies=_auth`. This
    validates the caller has a valid JWT, API key, or session cookie but does
    NOT inject a User object. Routes that need the actual user use
    `Depends(get_current_user)` in their function signatures.
  - Public routes (no auth): auth_router (login/register), health_router
    (liveness probes), pb_feed_router (PhantomBuster fetches lead data directly),
    newsletter_unsub_public_router (unsubscribe links clicked from emails).
  - Per-endpoint auth: orchestration_router handles its own auth internally.

Route registration order matters in two places:
  1. /unsubscribes/import MUST be registered before the unsubscribes sub-router
     because unsubscribes has a /{email} catch-all that would shadow /import.
  2. newsletter_unsub_public_router MUST be before newsletters_router because
     newsletters has a /{newsletter_id} catch-all.

Related files:
  - src/api/auth.py       — require_api_key, get_current_user dependencies
  - main.py               — mounts api_router on the FastAPI app
  - Each router module    — defines the actual endpoint handlers
"""

from fastapi import APIRouter, Depends, File, UploadFile
from .prospects import router as prospects_router
from .campaigns import router as campaigns_router
from .style import router as style_router
from .analytics import router as analytics_router
from .conference import router as conference_router
from .outlook_auth import router as outlook_auth_router
from .jobs import router as jobs_router
from .voice import router as voice_router
from .settings import router as settings_router, project_search_router
from .unsubscribes import router as unsubscribes_router, import_suppression_file
from .limits import router as limits_router
from .phantombuster import router as phantombuster_router
from .health import router as health_router
from .pb_feed import router as pb_feed_router
from .exclusions import router as exclusions_router
from .sequence_templates import router as sequence_templates_router
from .sequence_configs import router as sequence_configs_router
from .app_config import router as app_config_router
from .credentials import router as credentials_router
from .linkedin_accounts import router as linkedin_accounts_router
from .gtm_analytics import router as gtm_analytics_router
from .newsletters import router as newsletters_router, owners_router as newsletter_owners_router, unsub_public_router as newsletter_unsub_public_router
from .auth import require_api_key, get_current_user, router as auth_router
from ..orchestration.api import router as orchestration_router
from ..db.database import get_db
from ..db.models import User
from sqlalchemy.orm import Session

# Default auth dependency applied to all protected routers. Validates the
# caller has a valid credential but does not inject a User object.
_auth = [Depends(require_api_key)]

api_router = APIRouter(prefix="/api")

# ── Public routes (no auth) ──────────────────────────────────────────────────
# Auth routes must be public — login/register can't require an existing session.
api_router.include_router(auth_router)

# ── Suppression import (must come before unsubscribes router) ────────────────
# The unsubscribes router has a /{email} path parameter that would shadow the
# static /import path. Registering this first ensures /unsubscribes/import
# matches correctly.
@api_router.post("/unsubscribes/import", dependencies=_auth, tags=["unsubscribes"])
async def _suppression_import(file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return await import_suppression_file(file=file, db=db, current_user=current_user)

# ── Protected routes (require auth) ─────────────────────────────────────────
api_router.include_router(prospects_router, dependencies=_auth)       # Prospect CRUD, import, search, enrichment
api_router.include_router(campaigns_router, dependencies=_auth)       # Campaign CRUD, launch, pause, cancel, AI generation
api_router.include_router(style_router, dependencies=_auth)           # Writing style analysis & sync
api_router.include_router(analytics_router, dependencies=_auth)       # Dashboard analytics (send rates, engagement)
api_router.include_router(conference_router, dependencies=_auth)      # Conference outreach (leads, drafts, replies)
api_router.include_router(outlook_auth_router, dependencies=_auth)    # Microsoft Graph OAuth flow for Outlook access
api_router.include_router(jobs_router, dependencies=_auth)            # Background job status polling
api_router.include_router(voice_router, dependencies=_auth)           # Brand voice / tone settings
api_router.include_router(settings_router, dependencies=_auth)        # App settings (writing style, copy rules, etc.)
api_router.include_router(project_search_router, dependencies=_auth)  # DuckDuckGo news search for prospect companies
api_router.include_router(unsubscribes_router, dependencies=_auth)    # Suppression list CRUD
api_router.include_router(limits_router, dependencies=_auth)          # API credit usage limits
api_router.include_router(phantombuster_router, dependencies=_auth)   # PhantomBuster agent status & manual launch
api_router.include_router(exclusions_router, dependencies=_auth)      # Domain exclusion list (competitors, partners)
api_router.include_router(sequence_templates_router, dependencies=_auth)  # Saved reusable message templates
api_router.include_router(sequence_configs_router, dependencies=_auth)    # Saved sequence structures (step types + day offsets)
api_router.include_router(app_config_router, dependencies=_auth)      # Sequence shape constants for the dashboard JS
api_router.include_router(credentials_router, dependencies=_auth)     # Per-user encrypted service credentials (PB API keys)
api_router.include_router(linkedin_accounts_router, dependencies=_auth)  # LinkedIn accounts for multi-account automation
api_router.include_router(gtm_analytics_router, dependencies=_auth)   # GTM analytics (meetings, referrals, inbound)
# Newsletter unsubscribe links are clicked from emails — no auth required.
# MUST be before newsletters_router to avoid /{newsletter_id} catch-all shadow.
api_router.include_router(newsletter_unsub_public_router)
api_router.include_router(newsletters_router, dependencies=_auth)     # Newsletter CRUD, send, schedule
api_router.include_router(newsletter_owners_router, dependencies=_auth)  # Newsletter owner management

# ── Special routes ───────────────────────────────────────────────────────────
api_router.include_router(health_router)          # No auth — liveness probes need to work unauthenticated
api_router.include_router(pb_feed_router)          # No auth — PhantomBuster fetches lead URLs directly from this endpoint
api_router.include_router(orchestration_router)    # Auth handled per-endpoint internally (Fix 7)
