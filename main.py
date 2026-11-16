"""
Application entrypoint for Example's B2B outreach automation system.

This file bootstraps the entire FastAPI application:
  1. Configures structured logging (JSON lines to file + human-readable to console)
  2. Initialises the database (create tables, run migrations, seed admin user)
  3. Sets up rate limiting, CORS, security headers, and request logging middleware
  4. Launches background daemon threads on startup via the FastAPI lifespan:
     - Orchestration scheduler (advances LinkedIn sequences every N seconds)
     - LinkedIn worker (drains the linkedin_queue via PhantomBuster)
     - Conference reply poller (checks Outlook for replies to conference outreach)
     - Inbound lead poller (monitors sales@ shared mailbox for new inbound leads)
     - Follow-ups poller (monitors HubSpot deals, creates Trello cards for stale ones)
     - Newsletter scheduler (sends due newsletters via Graph API)
     - HubSpot sync scheduler (weekly sync of newsletter recipients from HubSpot)
  5. Serves the single-page dashboard (SPA) with session cookie auth
  6. Mounts the API router (all /api/* endpoints)

Architecture:
  - Single-process, multi-threaded: FastAPI handles HTTP; background work runs in
    daemon threads that share the same DB session factory.
  - PostgreSQL in production (VPS at /opt/outreach-system), SQLite for local dev/tests.
  - Deployed via SCP + `systemctl restart outreach.service`.

Related files:
  - src/api/router.py        — registers all API endpoint routers
  - src/config.py            — all tuneable constants and env var parsing
  - src/db/database.py       — SQLAlchemy engine, session factory, migrations
  - src/db/models.py         — ORM models (the data schema)
  - src/orchestration/       — LinkedIn sequence state machine + scheduler
  - src/services/            — background workers (linkedin_worker, newsletter_sender, etc.)
  - dashboard/               — static SPA files (index.html, main.js, styles.css)
"""

import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
# override=True ensures .env values win over any pre-existing env vars (e.g. from
# systemd EnvironmentFile or parent process). This is critical on the VPS where
# systemd may inject stale values from a previous deploy.
load_dotenv(override=True)

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from src.config import RATE_LIMIT_DEFAULT


# ── Structured logging setup ────────────────────────────────────────────────
# Two log outputs:
#   1. Console (human-readable) — for systemd journal and local dev
#   2. Rotating JSON file (logs/outreach.jsonl) — for structured log analysis
#      and the watchdog service that monitors for error spikes

class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line for easy parsing by log aggregation tools."""
    def format(self, record):
        log_entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


# P1-5: guard against double-configuration. Under gunicorn --preload or any
# code path that imports main.py more than once (e.g. tests), re-entering
# `_setup_logging()` used to attach a second pair of handlers to the root
# logger, causing every line to be emitted twice on stdout and in the JSON
# file. The marker attribute on the root logger makes the function idempotent.
_LOGGING_MARKER = "_outreach_logging_configured"


def _setup_logging():
    """Configure root logger with console + rotating file output.

    Idempotent - multiple calls are a no-op after the first. Existing
    handlers from a previous call are left in place so that loggers created
    between calls keep working."""
    root = logging.getLogger()
    if getattr(root, _LOGGING_MARKER, False):
        return
    root.setLevel(logging.INFO)

    # Belt-and-suspenders: if any foreign code (e.g. anthropic SDK,
    # uvicorn --preload) already attached a StreamHandler to root, don't
    # pile another one on top.
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        root.addHandler(console)

    # File handler - JSON lines, 5 MB rotation, keep 5 backups
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "outreach.jsonl",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(JSONFormatter())
        root.addHandler(file_handler)

    setattr(root, _LOGGING_MARKER, True)


_setup_logging()
logger = logging.getLogger(__name__)

# ── Session token derivation ────────────────────────────────────────────────
# The dashboard SPA authenticates via a __session cookie. This token must be
# deterministic (derived from API_KEY via HMAC) so that:
#  - All Gunicorn/uvicorn workers share the same token (no 401 roulette).
#  - Server restarts don't invalidate existing browser sessions.
#  - The token is never written to disk or HTML source.
#  - Without the API_KEY no valid token can be forged.
# If API_KEY is not set (local dev), a random ephemeral token is generated.
_API_KEY = os.getenv("API_KEY", "")
if not _API_KEY:
    import secrets as _secrets
    _SESSION_TOKEN = _secrets.token_urlsafe(32)
    logger.warning(
        "API_KEY is not set - session token is ephemeral and will change on restart. "
        "Set API_KEY in .env for stable multi-worker deployments."
    )
else:
    _SESSION_TOKEN = hmac.new(
        _API_KEY.encode(), b"dashboard-session-v1", hashlib.sha256
    ).hexdigest()

# ── Database initialisation ──────────────────────────────────────────────────
# init_db() creates all SQLAlchemy tables (if missing) and runs the hardcoded
# migration list in database.py to add columns/indexes that were added after
# initial deployment. This is safe to call multiple times (idempotent).
from src.db.database import init_db, SessionLocal
init_db()

# Seed writing style from file into DB if DB is empty (one-time migration).
# The writing style guide is used by Claude when generating outreach copy.
from src.api.settings import seed_writing_style_if_empty
_seed_db = SessionLocal()
try:
    seed_writing_style_if_empty(_seed_db)
finally:
    _seed_db.close()

from src.api.router import api_router
from src.api.auth import set_session_token
set_session_token(_SESSION_TOKEN)

# ── Seed default admin user ──────────────────────────────────────────────────
# On first deployment, create the admin user from ADMIN_EMAIL / ADMIN_PASSWORD
# env vars. This ensures the system is usable immediately without manual DB work.
# Subsequent restarts are no-ops (the user already exists).
from src.db.models import User as _SeedUser
_seed_db = SessionLocal()
try:
    if _seed_db.query(_SeedUser).count() == 0:
        _admin_email = os.getenv("ADMIN_EMAIL", "").strip()
        _admin_pass = os.getenv("ADMIN_PASSWORD", "").strip()
        if _admin_email and _admin_pass:
            import bcrypt as _bcrypt
            _seed_db.add(_SeedUser(
                id=str(__import__("uuid").uuid4()),
                name=os.getenv("ADMIN_NAME", "Admin"),
                email=_admin_email.lower(),
                password_hash=_bcrypt.hashpw(_admin_pass.encode(), _bcrypt.gensalt()).decode(),
                role="admin",
            ))
            _seed_db.commit()
            logger.info(f"Seeded admin user: {_admin_email}")
finally:
    _seed_db.close()

# Auto-sync LinkedIn accounts from PhantomBuster on every startup.
# Reads all PB agents, groups by session cookie (different cookie = different
# LinkedIn account), and creates/updates LinkedInAccount rows automatically.
# No manual configuration needed — accounts are discovered from PB directly.
try:
    if os.getenv("PHANTOMBUSTER_API_KEY"):
        _sync_db = SessionLocal()
        try:
            from src.api.linkedin_accounts import _auto_sync_from_pb
            _auto_sync_from_pb(_sync_db)
        finally:
            _sync_db.close()
    else:
        logger.warning("PHANTOMBUSTER_API_KEY not set — skipping LinkedIn account auto-sync")
except Exception as _sync_exc:
    logger.warning("LinkedIn account auto-sync failed: %s", _sync_exc)

# Legacy fallback: if auto-sync found nothing, seed from env vars
from src.db.models import LinkedInAccount as _SeedLinkedInAccount
_seed_db = SessionLocal()
try:
    if _seed_db.query(_SeedLinkedInAccount).count() == 0:
        _connect_id = os.getenv("PHANTOMBUSTER_CONNECT_AGENT_ID", "").strip()
        _message_id = os.getenv("PHANTOMBUSTER_MESSAGE_AGENT_ID", "").strip()
        _inbox_id = os.getenv("PHANTOMBUSTER_INBOX_SCRAPER_AGENT_ID", "").strip()
        _salesnav_id = os.getenv("PHANTOMBUSTER_SALESNAV_AGENT_ID", "").strip()
        if _connect_id or _message_id:
            _seed_db.add(_SeedLinkedInAccount(
                name="Default Account",
                connect_agent_id=_connect_id or None,
                message_agent_id=_message_id or None,
                inbox_scraper_agent_id=_inbox_id or None,
                sales_nav_agent_id=_salesnav_id or None,
                is_active=True,
            ))
            _seed_db.commit()
            logger.info("Seeded default LinkedIn account from env vars")
finally:
    _seed_db.close()

# Re-run the NULL user_id backfill now that an admin user is guaranteed to
# exist. The backfill is idempotent - a no-op if everything is already owned.
try:
    from src.db.database import _backfill_null_user_ids
    _backfill_null_user_ids()
except Exception as _e:  # never block startup on backfill failures
    logger.warning("Post-seed backfill skipped: %s", _e)

# P3-3: one-time migration of the legacy token_cache.json into tokens/
# outlook_{first_admin}.json. Idempotent: skips if the legacy file is
# missing or the per-user target already exists.
try:
    _legacy_tok = Path(__file__).parent / "token_cache.json"
    if _legacy_tok.exists():
        _tok_db = SessionLocal()
        try:
            _first_admin = _tok_db.query(_SeedUser).filter_by(role="admin").order_by(_SeedUser.created_at.asc()).first()
        finally:
            _tok_db.close()
        if _first_admin:
            _tok_dir = Path(__file__).parent / "tokens"
            _tok_dir.mkdir(parents=True, exist_ok=True)
            _target = _tok_dir / f"outlook_{_first_admin.id}.json"
            if not _target.exists():
                _legacy_tok.rename(_target)
                logger.info("P3-3: migrated legacy token_cache.json to %s", _target)
except Exception as _e:
    logger.warning("P3-3 Outlook token migration skipped: %s", _e)

# Rate limiter - keyed by IP address
limiter = Limiter(key_func=get_remote_address, default_limits=[RATE_LIMIT_DEFAULT])

_scheduler_thread: "threading.Thread | None" = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background scheduler + LinkedIn worker on startup; drain on shutdown.

    P1-3: On SIGTERM the lifespan context exits, we signal both the email
    scheduler and the LinkedIn worker, then join each thread with a 30s
    timeout so in-flight work can finish cleanly.

    P2-3: The LinkedIn worker is a SEPARATE thread from the email scheduler
    so a long Phantom poll never blocks email sequence advancement (deep
    audit CRITICAL-1).
    """
    global _scheduler_thread

    # ── Startup sweep: booking-link sanitisation ──────────────────────────
    # 2026-04-10 Nasser v1.1 is.gd incident: layer 4 of booking-link
    # hardening. On every service startup, sweep every EmailSequence row
    # that hasn't been sent yet and scrub any anchor tags / URL shorteners
    # that slipped through the other layers. Loud log warning per hit so
    # we notice if some upstream path is producing them again. Read-modify
    # commit is idempotent; running it twice is a no-op.
    try:
        import re as _isgd_re
        from src.db.database import SessionLocal as _IsgdSession
        from src.db.models import EmailSequence as _IsgdSeq, SequenceStatus as _IsgdStatus, AppSetting as _IsgdAppSetting
        _ANCHOR_RE = _isgd_re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>[^<]*</a>', _isgd_re.IGNORECASE)
        _MARKDOWN_LINK_RE = _isgd_re.compile(r'\[([^\]]*)\]\((https?://[^)]+)\)')
        _SHORT_RE = _isgd_re.compile(
            r'https?://(?:www\.)?(?:is\.gd|bit\.ly|tinyurl\.com|t\.co|buff\.ly|'
            r'goo\.gl|ow\.ly|rebrand\.ly|cutt\.ly|shorturl\.at|rb\.gy|tiny\.cc|'
            r's\.id|lnkd\.in)/\S+',
            _isgd_re.IGNORECASE,
        )
        from src.config import MAX_SEQUENCE_EMAILS as _ISGD_MAX
        _ISGD_FIELDS = tuple(
            f'email{n}_{part}'
            for n in range(1, _ISGD_MAX + 1)
            for part in ('subject', 'body')
        ) + ('linkedin_connect_note', 'linkedin_message')
        _isgd_db = _IsgdSession()
        try:
            _isgd_real_row = _isgd_db.query(_IsgdAppSetting).filter_by(key='calendly_link').first()
            _isgd_real = (_isgd_real_row.value or '').strip() if _isgd_real_row else ''
            # Only touch sequences that haven't been sent yet - we never
            # rewrite history, only future sends.
            _isgd_rows = (
                _isgd_db.query(_IsgdSeq)
                .filter(_IsgdSeq.status == _IsgdStatus.pending)
                .all()
            )
            _isgd_fixed = 0
            for _r in _isgd_rows:
                _dirty = False
                for _f in _ISGD_FIELDS:
                    _v = getattr(_r, _f, None) or ''
                    if not _v:
                        continue
                    if '<a href' in _v or _SHORT_RE.search(_v) or '[' in _v:
                        _new = _MARKDOWN_LINK_RE.sub(lambda m: m.group(2), _v)
                        _new = _ANCHOR_RE.sub(lambda m: m.group(1), _new)
                        if _isgd_real:
                            _new = _SHORT_RE.sub(_isgd_real, _new)
                        else:
                            _new = _SHORT_RE.sub('', _new)
                        if _new != _v:
                            setattr(_r, _f, _new)
                            _dirty = True
                if _dirty:
                    _isgd_fixed += 1
            if _isgd_fixed:
                _isgd_db.commit()
                logger.warning(
                    "Startup sweep: scrubbed %d pending EmailSequence row(s) containing "
                    "<a href> anchor tags or URL shorteners. Investigate upstream.",
                    _isgd_fixed,
                )
            else:
                logger.info("Startup sweep: all pending sequences clean (no anchors/shorteners).")
        finally:
            _isgd_db.close()
    except Exception as _sweep_exc:
        # Never let the sweep block startup - log and continue.
        logger.warning("Startup booking-link sweep failed: %s", _sweep_exc)

    # ── Seed copy rule defaults ────────────────────────────────────────────
    # Ensure email copy rules (word limits, forbidden phrases, etc.) exist
    # in app_settings so the email generation prompts have guardrails.
    try:
        from src.db.database import SessionLocal
        _seed_db = SessionLocal()
        try:
            from src.api.settings import seed_copy_defaults
            seed_copy_defaults(_seed_db)
        finally:
            _seed_db.close()
    except Exception as _seed_exc:
        logger.warning("Startup copy defaults seed failed: %s", _seed_exc)

    # ── Background thread startup ──────────────────────────────────────────
    # Each worker runs as a daemon thread so it dies automatically on process
    # exit. Each can be individually disabled via DISABLE_<NAME> env vars for
    # debugging or resource-constrained environments.

    # 1. Orchestration scheduler — advances LinkedIn sequences every N seconds.
    #    This is the heartbeat of the outreach engine: it checks which leads
    #    have a step due, dispatches the action (queue a connect, queue a
    #    message), and records the result.
    if os.getenv("DISABLE_SCHEDULER", "").lower() not in ("true", "1", "yes"):
        _scheduler_thread = threading.Thread(
            target=_scheduler_loop, name="scheduler-daemon", daemon=True
        )
        _scheduler_thread.start()
        logger.info("Background scheduler started (interval=%ds).", _SCHEDULER_INTERVAL)
    else:
        logger.info("Background scheduler disabled via DISABLE_SCHEDULER env var.")

    # 2. LinkedIn worker — drains linkedin_queue rows by launching PhantomBuster
    #    Phantoms and polling for results. Runs in its own thread so a long
    #    Phantom poll never blocks the orchestration scheduler.
    if os.getenv("DISABLE_LINKEDIN_WORKER", "").lower() not in ("true", "1", "yes"):
        from src.services.linkedin_worker import start_linkedin_worker
        start_linkedin_worker()
    else:
        logger.info("LinkedIn worker disabled via DISABLE_LINKEDIN_WORKER env var.")

    # 3. Conference reply poller — checks Outlook (via Graph API) for replies
    #    to conference outreach emails. When a lead replies, it sets replied_at
    #    on their ConferenceLead row to prevent further follow-up sends.
    if os.getenv("DISABLE_CONFERENCE_REPLY_POLLER", "").lower() not in ("true", "1", "yes"):
        from src.services.conference_reply_poller import start_conference_reply_poller
        start_conference_reply_poller()
    else:
        logger.info("Conference reply poller disabled via DISABLE_CONFERENCE_REPLY_POLLER env var.")

    # 4. Inbound lead poller — reads the sales@invigilo shared mailbox and
    #    auto-registers new external senders as inbound leads in GtmManualEntry.
    if os.getenv("DISABLE_INBOUND_LEAD_POLLER", "").lower() not in ("true", "1", "yes"):
        from src.services.inbound_lead_poller import start_inbound_lead_poller
        start_inbound_lead_poller()
    else:
        logger.info("Inbound lead poller disabled via DISABLE_INBOUND_LEAD_POLLER env var.")

    # 5. Follow-ups poller — monitors HubSpot deals for inactivity and creates
    #    Trello cards when deals go stale, so sales reps get actionable reminders.
    if os.getenv("DISABLE_FOLLOWUPS_POLLER", "").lower() not in ("true", "1", "yes"):
        from src.services.followups_poller import start_followups_poller
        start_followups_poller()
    else:
        logger.info("Followups poller disabled via DISABLE_FOLLOWUPS_POLLER env var.")

    # 6. Newsletter scheduler — checks for due scheduled newsletters every 60s
    #    and sends them via Microsoft Graph API (Outlook).
    if os.getenv("DISABLE_NEWSLETTER_SCHEDULER", "").lower() not in ("true", "1", "yes"):
        from src.services.newsletter_sender import start_newsletter_scheduler
        start_newsletter_scheduler()
    else:
        logger.info("Newsletter scheduler disabled via DISABLE_NEWSLETTER_SCHEDULER env var.")

    # 7. HubSpot sync scheduler — weekly auto-sync of newsletter recipient
    #    lists from HubSpot CRM contacts. Requires HUBSPOT_API_TOKEN in .env.
    if os.getenv("DISABLE_HUBSPOT_SYNC", "").lower() not in ("true", "1", "yes"):
        if os.getenv("HUBSPOT_API_TOKEN"):
            from src.services.newsletter_sender import start_hubspot_sync_scheduler
            start_hubspot_sync_scheduler()
        else:
            logger.warning("HUBSPOT_API_TOKEN not set — HubSpot newsletter sync scheduler disabled.")
    else:
        logger.info("HubSpot sync scheduler disabled via DISABLE_HUBSPOT_SYNC env var.")

    # 8. LinkedIn reply poller — detects prospect replies via PB Inbox Scraper
    if os.getenv("DISABLE_LINKEDIN_REPLY_POLLER", "").lower() not in ("true", "1", "yes"):
        if os.getenv("PHANTOMBUSTER_API_KEY"):
            from src.services.linkedin_reply_poller import start_linkedin_reply_poller
            start_linkedin_reply_poller()
        else:
            logger.warning("PHANTOMBUSTER_API_KEY not set — LinkedIn reply poller disabled.")
    else:
        logger.info("LinkedIn reply poller disabled via DISABLE_LINKEDIN_REPLY_POLLER env var.")

    yield  # ← Application runs here; everything below is shutdown cleanup

    # ── Graceful shutdown ────────────────────────────────────────────────
    # On SIGTERM (systemd stop), the lifespan context exits. We signal each
    # background thread to stop, then join with a timeout so in-flight work
    # can finish cleanly. Order: LinkedIn worker first (independent), then
    # the rest, then the scheduler thread last.
    # Signal scheduler thread to stop gracefully
    _stop_event.set()
    logger.info("Scheduler stop signal sent.")

    # P2-3: stop the LinkedIn worker FIRST so its in-flight Phantom poll
    # gets the cancel signal while the email scheduler is still draining.
    # The two are independent so the order is just for clarity.
    try:
        from src.services.linkedin_worker import stop_linkedin_worker
        stop_linkedin_worker(timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.warning("linkedin_worker shutdown error: %s", exc)

    # Events-reply-cutoff 2026-04-09: stop the conference reply poller.
    try:
        from src.services.conference_reply_poller import stop_conference_reply_poller
        stop_conference_reply_poller(timeout=15)
    except Exception as exc:  # noqa: BLE001
        logger.warning("conference_reply_poller shutdown error: %s", exc)

    try:
        from src.services.inbound_lead_poller import stop_inbound_lead_poller
        stop_inbound_lead_poller(timeout=15)
    except Exception as exc:  # noqa: BLE001
        logger.warning("inbound_lead_poller shutdown error: %s", exc)

    try:
        from src.services.followups_poller import stop_followups_poller
        stop_followups_poller(timeout=15)
    except Exception as exc:  # noqa: BLE001
        logger.warning("followups_poller shutdown error: %s", exc)

    # Session 5: stop newsletter scheduler
    try:
        from src.services.newsletter_sender import stop_newsletter_scheduler
        stop_newsletter_scheduler(timeout=15)
    except Exception as exc:  # noqa: BLE001
        logger.warning("newsletter_scheduler shutdown error: %s", exc)

    # Stop HubSpot sync scheduler
    try:
        from src.services.newsletter_sender import stop_hubspot_sync_scheduler
        stop_hubspot_sync_scheduler(timeout=10)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hubspot_sync_scheduler shutdown error: %s", exc)

    # Stop LinkedIn reply poller
    try:
        from src.services.linkedin_reply_poller import stop_linkedin_reply_poller
        stop_linkedin_reply_poller(timeout=10)
    except Exception as exc:  # noqa: BLE001
        logger.warning("linkedin_reply_poller shutdown error: %s", exc)

    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        _scheduler_thread.join(timeout=30)
        if _scheduler_thread.is_alive():
            logger.warning(
                "Scheduler thread did not drain within 30s - exiting anyway. "
                "In-flight work may be interrupted. See deep audit HIGH-1."
            )
        else:
            logger.info("Scheduler thread drained cleanly.")


# ── FastAPI app creation ─────────────────────────────────────────────────────
app = FastAPI(
    title="Outreach System",
    description="Apollo + Instantly + PhantomBuster + Claude-powered cold email and LinkedIn outreach",
    version="1.0.0",
    lifespan=lifespan,  # lifespan manages background thread startup/shutdown (above)
)

# SlowAPI rate limiter — keyed by client IP, default limit from config.py
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── CORS policy ──────────────────────────────────────────────────────────────
# Allow requests from the dashboard's own origin. Override with CORS_ORIGINS
# env var for production (comma-separated list of allowed origins).
from fastapi.middleware.cors import CORSMiddleware

_cors_origins_str = os.getenv("CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()] if _cors_origins_str else []

# FIX-1: When CORS_ORIGINS is set, lock the policy to those origins with
# credentialed requests allowed (cookie-based session auth). When unset, fall
# back to a wildcard regex BUT force allow_credentials=False - browsers reject
# `*` + credentials, and we don't want a foreign origin to silently inherit
# the dashboard cookie just because the operator forgot to set CORS_ORIGINS.
# This keeps the running prod campaign alive (no startup raise) while shutting
# down the impersonation hole the regression introduced.
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    logger.critical(
        "CORS_ORIGINS is not set - running with wildcard origin and "
        "allow_credentials=False. Set CORS_ORIGINS in .env "
        "(comma-separated allowed origins) to enable cookie-based auth from the dashboard."
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r".*",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# FIX-29: When neither API_KEY nor JWT_SECRET is set in non-development
# environments, log CRITICAL but never raise - production must keep serving.
_env_name = os.getenv("ENV", "development").lower()
if _env_name != "development" and not os.getenv("API_KEY") and not os.getenv("JWT_SECRET"):
    logger.critical(
        "ENV=%s but neither API_KEY nor JWT_SECRET is configured. "
        "JWT signing keys are ephemeral and sessions will not survive a restart. "
        "Set JWT_SECRET in .env (e.g. `openssl rand -hex 32`).",
        _env_name,
    )


# ── Request logging middleware ───────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log method, path, status code, and duration for all /api/ requests.
    Static asset requests are excluded to reduce noise."""
    start = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000, 1)
    if request.url.path.startswith("/api/"):
        logger.info(
            "%s %s → %d (%sms)",
            request.method, request.url.path, response.status_code, duration_ms,
        )
    return response


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to every response: XSS protection, clickjacking
    prevention, and a permissive CSP that allows the dashboard's inline
    scripts/styles and Chart.js from the CDN."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # CSP is permissive: inline styles/scripts needed by dashboard, Chart.js from CDN
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    )
    return response


# Mount all API routes under /api prefix (see src/api/router.py for the full map)
app.include_router(api_router)


# ── Background scheduler daemon ────────────────────────────────────────────
# The orchestration scheduler is the core engine that advances LinkedIn outreach
# sequences. It runs every N seconds (default 5 min) in a background thread.
#
# On each tick it:
#   1. Iterates all registered users (multi-tenant isolation)
#   2. For each user, builds a per-user ActionDispatcher with their PhantomBuster creds
#   3. Calls run_scheduler() which finds due orch_sequence_states and executes the
#      next step (queue a LinkedIn connect, queue a message, etc.)
#   4. Also runs the conference autodraft sweep (creates Outlook drafts for due
#      conference follow-up emails)
#
# The scheduler does NOT send LinkedIn requests directly — it writes to the
# linkedin_queue table. The linkedin_worker thread (above) drains that queue
# by launching PhantomBuster Phantoms.

_SCHEDULER_INTERVAL = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "300"))  # default 5 min

# Threading event used to signal the scheduler loop to stop. Also used as
# a sleep mechanism: _stop_event.wait(N) returns immediately when set,
# allowing fast shutdown instead of waiting out the full interval.
_stop_event = threading.Event()

# Pre-import scheduler dependencies outside the hot loop to avoid the ~50ms
# import-lock overhead on every tick (and shutdown hangs when the import lock
# is held during teardown).
from src.db.database import SessionLocal as _SchedulerSession  # noqa: E402
from src.orchestration.scheduler import run_scheduler as _run_scheduler  # noqa: E402
from src.orchestration.dispatcher import ActionDispatcher as _ActionDispatcher  # noqa: E402
from src.orchestration.api import _get_providers as _sched_get_providers  # noqa: E402
from src.orchestration.config import get_sequence_config as _sched_get_sequence_config  # noqa: E402


def _get_active_scheduler_user_ids(db) -> list[str]:
    """Return every user_id the scheduler should tick for this cycle.

    P3-4: in multi-tenant mode the loop iterates users rather than
    processing everything in a single global pass. We include every row
    in ``users`` (admins + reps) because a paused rep with a queued
    sequence still needs their tick to advance paused-aware state. For
    realistic rep counts (<10) the cost is trivial.

    Falls back to ``[None]`` - meaning "one legacy tick, no user filter"  -
    when the users table is empty or cannot be queried. This keeps
    first-run installs and test harnesses working without special-casing.
    """
    try:
        from src.db.models import User
        rows = db.query(User.id).order_by(User.created_at.asc()).all()
        ids = [r[0] for r in rows if r and r[0]]
        return ids or [None]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scheduler: could not read users list (%s) - falling back to legacy single-tick", exc)
        return [None]


def _scheduler_loop():
    """Background thread that runs the orchestration scheduler on an interval.

    P1-3 HIGH-1: checks `_stop_event` between sub-steps so shutdown can
    short-circuit a long tick instead of waiting for it to finish.

    P3-4 / deep audit CRITICAL-3: each interval now runs N sub-ticks, one
    per active user, each with a dispatcher built from that user's own
    provider cache entry. This is deliberately sequential - for realistic
    rep counts (3-6) parallelism adds thread-safety risk without meaningful
    throughput. Parallelise in a later phase if scale demands it.
    """
    while not _stop_event.is_set():
        try:
            db = _SchedulerSession()
            try:
                if _stop_event.is_set():
                    return

                active_user_ids = _get_active_scheduler_user_ids(db)
                total_processed = 0
                total_executed = 0
                total_skipped = 0
                total_failed = 0

                for user_id in active_user_ids:
                    if _stop_event.is_set():
                        return
                    try:
                        linkedin_provider = _sched_get_providers(
                            user_id=user_id, db=db,
                        )
                        dispatcher = _ActionDispatcher(
                            linkedin_provider=linkedin_provider,
                            db=db,
                            config_registry=_sched_get_sequence_config,
                        )
                        if _stop_event.is_set():
                            return
                        summary = _run_scheduler(
                            now=datetime.now(timezone.utc),
                            db=db,
                            dispatcher=dispatcher,
                            user_id=user_id,
                        )
                        total_processed += summary.states_processed
                        total_executed += summary.steps_executed
                        total_skipped += summary.steps_skipped
                        total_failed += summary.steps_failed

                        # Events-auto-draft 2026-04-10: for every active
                        # scheduler user, also sweep their ConferenceLead
                        # rows and auto-draft any sequence steps that have
                        # come due since the last tick. Wrapped in its own
                        # try/except so a flaky Outlook token or bad JSON
                        # on one conference lead cannot break the main
                        # campaign scheduler path for this user.
                        if user_id:
                            try:
                                from src.db.models import User as _User
                                _sweep_user = db.query(_User).filter_by(id=user_id).first()
                                if _sweep_user is not None:
                                    from src.api.conference.autodraft import (
                                        run_conference_autodraft_sweep as _run_autodraft,
                                    )
                                    _run_autodraft(db, _sweep_user)
                            except Exception as ad_exc:  # noqa: BLE001
                                logger.error(
                                    "Conference autodraft sweep failed user=%s: %s",
                                    user_id, ad_exc, exc_info=True,
                                )
                    except Exception as user_exc:  # noqa: BLE001
                        # One user's failure must not block the rest of the
                        # loop. The scheduler_last_run upsert still runs
                        # inside run_scheduler for the users that succeeded,
                        # so watchdog liveness isn't affected.
                        logger.error(
                            "Scheduler: tick failed for user=%s: %s",
                            user_id or "<legacy>", user_exc, exc_info=True,
                        )

                if total_processed > 0:
                    logger.info(
                        "Scheduler: processed %d states across %d user(s) "
                        "(%d executed, %d skipped, %d failed)",
                        total_processed, len(active_user_ids),
                        total_executed, total_skipped, total_failed,
                    )
            finally:
                db.close()
        except Exception as e:
            logger.error("Scheduler loop error: %s", e, exc_info=True)
        # `Event.wait` returns True as soon as the event is set, so shutdown
        # interrupts the interval sleep instead of waiting it out.
        _stop_event.wait(_SCHEDULER_INTERVAL)


# ── Dashboard SPA serving ────────────────────────────────────────────────────
# The dashboard is a single-page application (HTML + JS + CSS) served from the
# dashboard/ directory. The SPAMiddleware handles:
#   1. Serving static files (favicon, fonts, images) for any path under /assets/
#   2. Returning the SPA shell (index.html) for known dashboard routes so that
#      client-side routing (history API) works on page refresh
#   3. Injecting a __session cookie (derived from API_KEY) so the browser can
#      authenticate API requests without exposing the key to JavaScript
#   4. Cache-busting: styles.css and main.js get ?v=<mtime> query params so
#      browser caches are busted on every deploy
dashboard_path = Path(__file__).parent / "dashboard"

if dashboard_path.exists():
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import FileResponse as StarletteFileResponse, Response as StarletteResponse
    from starlette.requests import Request as StarletteRequest

    _dashboard_html_cache: str | None = None
    _dashboard_html_mtime: tuple = ()  # P5: composite (index_mtime, styles_v, main_v)

    def _asset_version(filename: str) -> str:
        """Return the integer mtime of an asset file under dashboard/, used as
        a cache-bust query param. P5: ensures browsers pick up new bundled
        ``styles.css`` / ``main.js`` after a deploy without manual cache
        clears. Falls back to ``"0"`` if the file is missing so the SPA
        still serves rather than 500ing on a misconfigured deploy."""
        try:
            return str(int((dashboard_path / filename).stat().st_mtime))
        except OSError:
            return "0"

    def _get_dashboard_html() -> str:
        """
        Read index.html, caching it in memory for performance.

        Cache is invalidated when the file changes on disk (mtime check),
        so edits to index.html take effect without a server restart.

        P5: substitutes ``__STYLES_VERSION__`` and ``__MAIN_VERSION__``
        placeholders with the on-disk mtimes of styles.css and main.js so
        every deploy busts the browser cache for those static assets.
        The shell index.html went from 9554 LOC down to ~1900 LOC after
        the CSS and JS were extracted into their own files; the Python
        side just keeps stitching the cache-bust versions in.
        """
        global _dashboard_html_cache, _dashboard_html_mtime
        index_file = dashboard_path / "index.html"
        if not index_file.exists():
            return "<html><body><h1>Dashboard not found</h1><p>index.html is missing from the dashboard directory.</p></body></html>"
        # Cache key includes the styles + main asset mtimes so a fresh
        # bundle on disk forces a re-render of the shell with the new
        # cache-bust query strings, even if index.html itself is unchanged.
        index_mtime = index_file.stat().st_mtime
        styles_v = _asset_version("styles.css")
        main_v = _asset_version("main.js")
        composite_mtime = (index_mtime, styles_v, main_v)
        if _dashboard_html_cache is None or composite_mtime != _dashboard_html_mtime:
            raw = index_file.read_text(encoding="utf-8")
            raw = raw.replace("__STYLES_VERSION__", styles_v)
            raw = raw.replace("__MAIN_VERSION__", main_v)
            _dashboard_html_cache = raw
            _dashboard_html_mtime = composite_mtime
        return _dashboard_html_cache

    # Known dashboard route prefixes (client-side SPA routes)
    _DASHBOARD_ROUTES = (
        "/", "/login", "/register", "/dashboard", "/prospects", "/campaigns",
        "/sequences", "/settings", "/style", "/email", "/analytics",
    )

    class SPAMiddleware(BaseHTTPMiddleware):
        """Catch-all middleware that turns 404s into SPA shell responses for known
        dashboard routes, or serves static files from the dashboard/ directory.
        This enables client-side routing: /campaigns, /settings, etc. all return
        index.html and let the JavaScript router handle the actual view."""
        async def dispatch(self, request: StarletteRequest, call_next):
            response = await call_next(request)
            if (
                response.status_code == 404
                and not request.url.path.startswith("/api/")
                and not request.url.path.startswith("/assets/")
            ):
                path = request.url.path.lstrip("/")
                static_file = (dashboard_path / path).resolve()
                # Prevent directory traversal: resolved path must stay inside dashboard dir
                if (
                    str(static_file).startswith(str(dashboard_path.resolve()))
                    and static_file.exists()
                    and static_file.is_file()
                ):
                    return StarletteFileResponse(str(static_file))
                # Only serve the SPA shell (with session cookie) for known
                # dashboard routes.  Arbitrary 404 paths should not receive
                # the cookie.
                req_path = request.url.path.rstrip("/") or "/"
                is_dashboard = req_path == "/" or any(
                    req_path == r or req_path.startswith(r + "/")
                    for r in _DASHBOARD_ROUTES if r != "/"
                )
                if not is_dashboard:
                    return response
                html_response = HTMLResponse(_get_dashboard_html())
                html_response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
                html_response.headers["Pragma"] = "no-cache"
                html_response.headers["Expires"] = "0"
                # P5: ETag derives from the composite mtime tuple so it
                # changes when index.html, styles.css, or main.js are updated.
                html_response.headers["ETag"] = f'"{hash(_dashboard_html_mtime)}"'
                html_response.set_cookie(
                    "__session",
                    _SESSION_TOKEN,
                    samesite="strict",
                    httponly=True,   # JS fetch() sends cookies automatically; value never exposed to JS
                    secure=request.url.scheme == "https",
                    path="/",
                )
                return html_response
            return response

    app.add_middleware(SPAMiddleware)
    app.mount("/assets", StaticFiles(directory=str(dashboard_path)), name="assets")


# ── Direct execution (python main.py) ────────────────────────────────────────
# For local development. In production, systemd runs this via:
#   ExecStart=/opt/outreach-system/.venv/bin/python main.py
if __name__ == "__main__":
    import signal
    import subprocess
    import sys
    import uvicorn

    port = int(os.getenv("PORT", 3004))  # Dashboard + API both served on this port
    reload = os.getenv("RELOAD", "false").lower() == "true"

    # Kill any stale process holding our port - prevents Errno 98 crash loops
    # after unclean shutdowns. Safe: only targets TCP listeners on our exact port.
    if sys.platform != "win32":
        try:
            subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True, timeout=5)
            time.sleep(0.5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # fuser not available or timed out - non-fatal

    logger.info(f"Starting Outreach System on http://localhost:{port}")
    reload_dirs = [str(Path(__file__).parent / "src"), str(Path(__file__).parent / "dashboard")] if reload else []
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload, reload_dirs=reload_dirs or None)
