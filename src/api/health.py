"""
Health check and system status endpoints.

Provides:
  GET /api/health          -  basic liveness (no auth)
  GET /api/health/full     -  deep check: DB, APIs, DNS, scheduler (auth required)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db.database import get_db
from .auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


@router.get("/")
def liveness():
    """Basic liveness probe  -  always returns 200 if the server is running."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/watchdog")
def watchdog_status():
    """Returns last watchdog check results (read from state file, no DB)."""
    state_file = Path(__file__).resolve().parent.parent.parent / "watchdog_state.json"
    if not state_file.exists():
        return {"status": "unknown", "detail": "Watchdog not running or no checks completed yet."}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": "error", "detail": f"Could not read watchdog state: {e}"}


@router.get("/full", dependencies=[Depends(require_api_key)])
def full_health_check(db: Session = Depends(get_db)):
    """
    Deep health check  -  verifies database, API keys, and DNS for sending domains.
    """
    checks = {}

    # 1. Database
    try:
        start = time.time()
        db.execute(text("SELECT 1"))
        db_ms = round((time.time() - start) * 1000, 1)
        checks["database"] = {"status": "ok", "latency_ms": db_ms}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)}

    # 2. API key presence (not validity  -  that would cost API calls)
    api_keys = {
        "apollo": bool(os.getenv("APOLLO_API_KEY")),
        "phantombuster": bool(os.getenv("PHANTOMBUSTER_API_KEY")),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        # FIX-31: actually report whether REACHER_BASE_URL is set, not "always true".
        "reacher": bool(os.getenv("REACHER_BASE_URL", "").strip()),
    }
    missing_keys = [k for k, v in api_keys.items() if not v]
    checks["api_keys"] = {
        "status": "ok" if not missing_keys else "warning",
        "configured": {k: v for k, v in api_keys.items()},
        "missing": missing_keys,
    }

    # 3. DNS check for sending domains (limited to first 3 to avoid blocking)
    dns_results = []
    try:
        from ..services.dns_check import check_domain, extract_domain
        from ..db.models import ConferenceInbox
        inboxes = db.query(ConferenceInbox).all()
        seen_domains = set()
        for inbox in inboxes:
            domain = extract_domain(inbox.email)
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                if len(seen_domains) > 3:
                    break  # Cap DNS lookups to avoid blocking
                try:
                    dns_results.append(check_domain(domain))
                except Exception as dns_err:
                    dns_results.append({"domain": domain, "overall": "error", "error": str(dns_err)})
        checks["dns"] = {
            "status": "ok" if all(d.get("overall") == "ok" for d in dns_results) else "warning",
            "domains": dns_results,
        } if dns_results else {"status": "skipped", "detail": "No sending inboxes configured"}
    except Exception as e:
        checks["dns"] = {"status": "error", "detail": str(e)}

    # 4. Scheduler last run
    try:
        from ..db.models import AppSetting
        setting = db.query(AppSetting).filter_by(key="scheduler_last_run").first()
        checks["scheduler"] = {
            "status": "ok" if setting else "warning",
            "last_run": setting.value if setting else None,
        }
    except Exception as e:
        checks["scheduler"] = {"status": "error", "detail": str(e)}

    # 5. Suppression stats
    try:
        from ..db.models import UnsubscribeEntry, ExcludedDomain
        unsub_count = db.query(UnsubscribeEntry).count()
        excluded_count = db.query(ExcludedDomain).count()
        checks["suppression"] = {
            "status": "ok",
            "unsubscribed_emails": unsub_count,
            "excluded_domains": excluded_count,
        }
    except Exception as e:
        checks["suppression"] = {"status": "error", "detail": str(e)}

    # 6. LinkedIn worker
    try:
        from ..services.linkedin_worker import linkedin_worker_alive
        alive = linkedin_worker_alive()
        checks["linkedin_worker"] = {
            "status": "ok" if alive else "warning",
            "running": alive,
        }
    except Exception as e:
        checks["linkedin_worker"] = {"status": "error", "detail": str(e)}

    # 7. Inbound lead poller
    try:
        from ..services.inbound_lead_poller import inbound_lead_poller_alive
        alive = inbound_lead_poller_alive()
        checks["inbound_lead_poller"] = {
            "status": "ok" if alive else "warning",
            "running": alive,
        }
    except Exception as e:
        checks["inbound_lead_poller"] = {"status": "error", "detail": str(e)}

    # 8. Followups poller
    try:
        from ..services.followups_poller import followups_poller_alive
        alive = followups_poller_alive()
        checks["followups_poller"] = {
            "status": "ok" if alive else "warning",
            "running": alive,
        }
    except Exception as e:
        checks["followups_poller"] = {"status": "error", "detail": str(e)}

    # Overall
    statuses = [c.get("status") for c in checks.values()]
    if "error" in statuses:
        overall = "unhealthy"
    elif "warning" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
