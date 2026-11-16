"""
Centralised configuration constants for the Example outreach system.

This is the single source of truth for all tuneable parameters. Every magic
number, model name, rate limit, word limit, and status classification lives
here so changes propagate automatically to all consumers.

Organisation:
  - Claude model IDs and pricing
  - Email validation status sets (which statuses are safe/invalid/uncertain)
  - Campaign and sequence shape limits (max emails, day offsets)
  - Email generation constraints (word limits, token budgets)
  - Project search (DuckDuckGo) parameters
  - Background job processing batch sizes
  - Style refinement limits
  - PhantomBuster API parameters and result schemas
  - Rate limiting (SlowAPI)
  - Reacher (self-hosted email verification) settings
  - Apollo credits
  - Orchestration scheduler parameters
  - LinkedIn worker and batch processor settings
  - Engagement event dedup windows
  - Watchdog thresholds (for the outreach-watchdog.service)

All values can be overridden via environment variables (see _int_env helper).
Defaults are tuned for production use on the VPS at /opt/outreach-system.

Related files:
  - main.py                      — imports RATE_LIMIT_DEFAULT for SlowAPI setup
  - src/orchestration/config.py  — sequence step definitions (references day offsets here)
  - src/services/linkedin_worker.py — reads LINKEDIN_* constants
  - src/services/email_gen_helpers.py — reads EMAIL_WORD_LIMITS, MAX_SEQUENCE_EMAILS
"""
import os


def _int_env(key: str, default: int) -> int:
    """Parse an integer env var with a clear error on bad values.

    Audit 2026-05-05 #72: bare ``int(os.getenv(...))`` crashes the app
    at import time with a cryptic ``ValueError`` if the .env entry is
    malformed (e.g. ``MAX_SEQUENCE_EMAILS=6.5``).  This wrapper gives
    the operator a one-line diagnostic that names the offending key.
    """
    raw = os.getenv(key)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"Invalid integer for env var {key}={raw!r}  "
            f"(expected a whole number, defaulting would be {default})"
        ) from None

# ── Claude model IDs ───────────────────────────────────────────────────────────
# Two tiers: FAST for quick classification/parsing, SMART for email/message
# generation where quality matters. Override with env vars for version bumps
# without code changes (e.g. MODEL_SMART=claude-sonnet-4-20250514).
MODEL_FAST = os.getenv("MODEL_FAST", "claude-haiku-4-5-20251001")   # reply classification, JSON parsing
MODEL_SMART = os.getenv("MODEL_SMART", "claude-sonnet-4-6")         # email/LinkedIn message generation

# ── Anthropic pricing (USD per million tokens) ─────────────────────────────────
# Update when Anthropic changes pricing. Keyed by model ID prefix for forward-compat.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6":          {"input": 3.0,  "output": 15.0},
    "claude-haiku-4-5-20251001":  {"input": 0.8,  "output": 4.0},
}
# Fallback if a model ID isn't in the table
DEFAULT_MODEL_PRICING = {"input": 3.0, "output": 15.0}


def get_model_pricing(model_id: str) -> dict[str, float]:
    """Return {input, output} USD/MTok rates for a given model ID."""
    return MODEL_PRICING.get(model_id, DEFAULT_MODEL_PRICING)

# ── Email validation status sets ──────────────────────────────────────────────
# These status strings come from the Reacher email verification service
# (self-hosted, see REACHER_DEFAULT_URL below). They classify an email address
# as deliverable, risky, or undeliverable. Used at campaign launch time to
# skip unverifiable addresses and at the dashboard to show validation badges.

# Statuses that are confirmed undeliverable — skip these at launch.
INVALID_EMAIL_STATUSES = frozenset({"invalid", "spamtrap", "abuse", "do_not_mail"})

# Statuses that are safe to send to.
VALID_EMAIL_STATUSES = frozenset({"valid"})

# Statuses that are uncertain — user's call whether to include.
# "catch-all" means the domain accepts all addresses (can't verify the specific mailbox).
UNCERTAIN_EMAIL_STATUSES = frozenset({"catch-all", "unknown"})

# ── Campaign limits ────────────────────────────────────────────────────────────
# Maximum prospects per campaign generation run.
# Raise this if you need to process larger batches (watch memory and API rate limits).
PROSPECT_CAP = 500

# ── Prospect statuses that are terminal (no re-enrollment) ────────────────────
# If a prospect has one of these statuses, recovering their email must NOT
# reset them to "pending" (they are already in-flight or finished).
TERMINAL_PROSPECT_STATUSES = frozenset({"enrolled", "connected", "messaged", "replied", "bounced"})

# ── Sequence shape limits (2026-04-11 hardcoded-values audit) ─────────────────
#
# Single source of truth for "how many email slots does an EmailSequence
# row have". Historically every loop in the codebase was hardcoded to
# ``range(1, 7)`` which meant raising the cap required touching ~10 sites
# across 5 files. All loops, validations, and UI defaults now derive from
# ``MAX_SEQUENCE_EMAILS``  -  the flat column schema is still the hard
# ceiling today (see models.py), but bumping MAX_SEQUENCE_EMAILS plus a
# single Alembic migration adding email7_subject/body is now the only
# change required to support a longer sequence.
MAX_SEQUENCE_EMAILS = _int_env("MAX_SEQUENCE_EMAILS", 6)

# Default day offsets for each email step when a campaign has no pinned
# sequence config (pre-0009 campaigns) and we need to infer a shape from
# the presence of populated email{n}_body columns. Mirrors the
# EXAMPLE_DEFAULT_SEQUENCE email offsets in src/orchestration/config.py.
DEFAULT_EMAIL_DAY_OFFSETS: list[int] = [1, 7, 12, 18, 24, 30]

# Default day offsets for the LinkedIn companion steps in a hybrid
# sequence. Used by _resolve_campaign_sequence_shape's inferred fallback.
DEFAULT_LINKEDIN_CONNECT_DAY = 4
DEFAULT_LINKEDIN_MESSAGE_DAY = 16

# ── Email generation ───────────────────────────────────────────────────────────
EMAIL_GEN_MAX_TOKENS = 3000
# Per-email word limits enforced after generation (key → max words).
# Slots beyond those explicitly listed fall back to
# ``DEFAULT_EMAIL_WORD_LIMIT`` via ``email_word_limit()`` so raising
# MAX_SEQUENCE_EMAILS doesn't silently bypass validation.
EMAIL_WORD_LIMITS: dict[str, int] = {
    "email1": 100, "email2": 60, "email3": 60,
    "email4": 60, "email5": 50, "email6": 50,
}
# Fallback word limit for email slots not explicitly listed above.
# Matches the tightest hand-tuned limit so a new slot can never
# accidentally relax validation  -  change only with product sign-off.
DEFAULT_EMAIL_WORD_LIMIT = 50
# LinkedIn message word limits (validated separately from emails).
LINKEDIN_WORD_LIMITS: dict[str, int] = {
    "linkedin_connect_note": 30, "linkedin_message": 50,
}


def email_word_limit(key: str) -> int:
    """Return the word limit for an email slot key, falling back to
    ``DEFAULT_EMAIL_WORD_LIMIT`` for slots beyond the hand-tuned set.

    Canonical accessor  -  callers must not iterate ``EMAIL_WORD_LIMITS``
    directly when deciding what to validate (use ``email_slot_keys()``
    for the authoritative slot list)."""
    return EMAIL_WORD_LIMITS.get(key, DEFAULT_EMAIL_WORD_LIMIT)


def email_slot_keys() -> list[str]:
    """Return the canonical list of email slot keys for the current cap.

    Always prefer this over hardcoded ``[f"email{n}" for n in range(1,7)]``
     -  callers using it automatically pick up any future bump to
    ``MAX_SEQUENCE_EMAILS``."""
    return [f"email{n}" for n in range(1, MAX_SEQUENCE_EMAILS + 1)]


def email_slot_range() -> range:
    """Return ``range(1, MAX_SEQUENCE_EMAILS + 1)`` for loops that work
    with step numbers (1-based) instead of string keys."""
    return range(1, MAX_SEQUENCE_EMAILS + 1)

# ── Project search (DuckDuckGo) ────────────────────────────────────────────────
PROJECT_SEARCH_LOOKBACK_DAYS = 180       # ~6 months
PROJECT_SEARCH_MAX_RESULTS = 8           # DDG news returns enough headroom for filtering
PROJECT_SNIPPET_MAX_CHARS = 250          # chars of search content fed to Claude
PROJECT_REPHRASE_MAX_TOKENS = 80         # Claude output budget for the rephrased sentence

# ── Background job processing ─────────────────────────────────────────────────
# How often to flush progress to the DB during generation.
# Commits every N prospects  -  reduces disk I/O from O(n) to O(n/batch).
JOB_PROGRESS_BATCH = 5
# Sequences are bulk-committed every N rows to reduce per-row round-trips.
SEQUENCE_COMMIT_BATCH = 25

# ── Style refine ──────────────────────────────────────────────────────────────
STYLE_REFINE_MAX_TOKENS = 2000
STYLE_REFINE_PROMPT_MAX_LEN = 500        # chars  -  prevents oversized injection payloads
STYLE_REFINE_STYLE_MAX_LEN = 8000        # chars  -  cap on the current style guide input

# ── PhantomBuster API ────────────────────────────────────────────────────────
PHANTOMBUSTER_API_TIMEOUT = 60           # seconds (Phantoms can take a while)
PHANTOMBUSTER_API_RETRIES = 3
PHANTOMBUSTER_RATE_LIMIT_DELAY = 0.25    # conservative throttle

# ── HTTP retry backoff (P2-1 / deep audit CRITICAL-4) ───────────────────────
# Exponential backoff ceiling (seconds) applied to transient-error retries
# across all provider clients (phantombuster, outlook).
HTTP_RETRY_BACKOFF_CEILING = _int_env("HTTP_RETRY_BACKOFF_CEILING", 30)

# ── Failure retry ladder (P2-2 / deep audit HIGH-4) ─────────────────────────
# When a LinkedIn step handler fails (e.g. PhantomBuster API timeout, rate
# limit), the orchestration dispatcher reschedules the sequence using this
# escalating delay ladder. Each entry is hours to wait before the next retry,
# indexed by consecutive_failure_count. After MAX_CONSECUTIVE_FAILURES the
# sequence is permanently paused with a reason string (requires manual review).
# Override at deploy time: RETRY_LADDER_HOURS=1,6,24
def _parse_retry_ladder(raw: str) -> list[int]:
    out: list[int] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.append(int(piece))
        except ValueError:
            continue
    return out or [1, 6]

RETRY_LADDER_HOURS: list[int] = _parse_retry_ladder(
    os.getenv("RETRY_LADDER_HOURS", "1,6")
)
MAX_CONSECUTIVE_FAILURES = _int_env("MAX_CONSECUTIVE_FAILURES", 3)

# ── PhantomBuster result schemas (P2-5 / deep audit HIGH-9) ─────────────────
# Defines how we interpret the JSON result rows returned by PhantomBuster
# Phantoms after a LinkedIn action completes. Each entry maps an action_type
# ("connect" or "message") to the field names we look for in the result.
#
# Why this matters: PB returns different field names depending on the Phantom
# type (Auto Connect vs Outbound Agent vs Message Sender) and even the version
# of the script. Without explicit schemas, we'd be guessing which fields
# indicate success/failure, leading to false-positive "done" marks or
# phantom-leads that silently never advance.
#
# The linkedin_worker (src/services/linkedin_worker.py) uses these schemas
# in classify_phantom_row() to decide: success → mark done, neutral → mark done
# (already connected), pending → leave in processing, error → mark failed.
#
# Schema fields:
#   url_field:     name of the row field that holds the LinkedIn profile URL
#                  (we accept the first non-empty match in priority order).
#   success_flags: row fields that, if truthy, indicate a successful action.
#   neutral_flags: row fields that, if truthy, mean the action was a no-op
#                  (already connected / already messaged)  -  still treated as
#                  done so the queue advances, but logged separately.
#   error_field:   row field that holds an error message; non-empty = failed.
#   accept_status: lowercased status string values that count as success.
#   neutral_status: lowercased status strings that count as no-op done.
PHANTOM_RESULT_SCHEMA: dict[str, dict] = {
    "connect": {
        # 2026-04-10: schema extended for the "Outbound Agent LinkedIn
        # Outreach" Phantom (script LinkedIn Outreach.js, agent
        # 3599314517037176). It returns rows with `linkedinProfileUrl`
        # and `connectionStatus`/`invitationSent` fields in addition to
        # the legacy `profileUrl`/`status` shape from the original Auto
        # Connect Phantom. Both shapes resolve to the same verdicts.
        #
        # "Not invited yet"  -  the Outbound Agent's intermediate status
        # while a profile is queued in its internal daily pacer but
        # hasn't been invited yet. It is NOT a failure; the Phantom
        # will send the invite later in the day. We treat it as
        # neutral (done) so the orch state advances past the connect
        # step and doesn't block the rest of the sequence. The alternative
        # (leaving it as unknown) would incorrectly pause every lead
        # ever queued through the Outbound Agent, because pacing means
        # "Not invited yet" is the steady state for most of the day.
        "url_field": ("profileUrl", "linkedinProfileUrl", "url", "linkedinUrl"),
        "success_flags": ("connectionRequestSent", "connected", "success", "invitationSent"),
        "neutral_flags": ("alreadyConnected", "alreadyInvited"),
        "error_field": ("error", "errorMessage"),
        "accept_status": frozenset({
            "sent", "connected", "request sent", "connection sent",
            "invitation sent", "success", "done",
        }),
        "neutral_status": frozenset({
            "already connected", "already sent", "already invited",
        }),
        # Statuses that mean PB knows the lead but hasn't processed it yet.
        # Previously "not invited yet" was in neutral_status which silently
        # marked leads as done. Now they stay in processing for the poller.
        "pending_status": frozenset({
            "pending", "not invited yet", "queued", "in queue",
        }),
    },
    "message": {
        "url_field": ("profileUrl", "url", "linkedinUrl", "linkedinProfileUrl"),
        "success_flags": ("messageSent", "sent", "success"),
        "neutral_flags": ("alreadyMessaged",),
        "error_field": ("error", "errorMessage"),
        "accept_status": frozenset({
            "sent", "message sent", "delivered", "success", "done",
        }),
        "neutral_status": frozenset({
            "already messaged", "duplicate",
        }),
        # PB's LinkedIn Message Sender phantom does NOT set messageSent
        # or status fields. The only reliable delivery indicator is the
        # presence of a timestamp + senderName in the result row (PB
        # only populates these when it actually sends the message).
        # See classify_phantom_row for the fallback check.
        "presence_success_fields": ("timestamp", "senderName"),
    },
}

# ── Rate limiting (SlowAPI) ───────────────────────────────────────────────────
RATE_LIMIT_DEFAULT = "200/minute"

# ── Reacher (self-hosted email verification) ─────────────────────────────────
REACHER_DEFAULT_URL = "http://localhost:8080"

# ── Apollo ────────────────────────────────────────────────────────────────────
# Override with APOLLO_MONTHLY_CREDITS env var if on a paid plan.
APOLLO_MONTHLY_CREDITS = _int_env("APOLLO_MONTHLY_CREDITS", 50)

# ── Orchestration scheduler (P1-7 / deep audit LOW-1) ─────────────────────────
# Maximum number of orch_sequence_states rows processed per scheduler tick.
# Prevents thundering herd if there is a large backlog (e.g. after downtime).
# With ~50-200 active leads this is rarely hit; raise only if scale demands it.
SCHEDULER_BATCH_LIMIT = _int_env("SCHEDULER_BATCH_LIMIT", 5000)

# Default per-sequence daily send cap used by the ORCHESTRATION scheduler
# when a step becomes due and no per-sequence override is set. This is
# completely independent from ``INSTANTLY_DEFAULT_DAILY_LIMIT``  -  that
# one is the default plugged into a brand new Instantly campaign at
# creation time. Precedence for the orchestrator is:
#   1. SequenceConfig.daily_send_limit (set by the builder UI)
#   2. this constant (env-overridable)
DEFAULT_DAILY_SEND_LIMIT = _int_env("DEFAULT_DAILY_SEND_LIMIT", 50)

# 2026-04-10: per-user LinkedIn connect daily cap. LinkedIn enforces ~100
# invites/week (roughly 20/weekday) and our Outbound Agent Phantom is
# configured to the same 20/day ceiling. We default to 18 to leave a
# 2-slot headroom for manual launches or slack across overlapping
# campaigns  -  going higher risks hitting the Phantom's internal cap and
# having the overflow leads come back as "unknown: no result row for
# url" (now correctly surfaced as paused-with-reason, but still a dead
# end for the affected leads until the next day).
#
# Env override: LINKEDIN_CONNECT_DAILY_CAP. Per-user override honoured
# via app_settings key `__user__{uid}__linkedin_connect_daily_cap`.
DEFAULT_LINKEDIN_CONNECT_DAILY_CAP = _int_env("LINKEDIN_CONNECT_DAILY_CAP", 18)

# ── PhantomBuster LinkedIn Profile Scraper ────────────────────────────────
# Replaces Proxycurl (shut down 2025) for LinkedIn profile enrichment.
# The scraper phantom extracts headline, summary, and company from a
# LinkedIn profile URL. It shares the PB execution slot with the
# LinkedIn Auto Connect phantom (Starter plan = 1 parallel execution).
PHANTOMBUSTER_SCRAPER_AGENT_ID = os.getenv(
    "PHANTOMBUSTER_SCRAPER_AGENT_ID", "7207870347238196"
)
PHANTOMBUSTER_SCRAPER_POLL_INTERVAL = _int_env("PHANTOMBUSTER_SCRAPER_POLL_INTERVAL", 10)
PHANTOMBUSTER_SCRAPER_POLL_TIMEOUT = _int_env("PHANTOMBUSTER_SCRAPER_POLL_TIMEOUT", 120)

# ── LinkedIn batch processor (P1-7 / deep audit LOW-1) ────────────────────────
# Maximum profiles per PhantomBuster Phantom launch (matches the API payload
# limit  -  do not raise above 50 without checking PhantomBuster docs).
LINKEDIN_BATCH_SIZE_LIMIT = _int_env("LINKEDIN_BATCH_SIZE_LIMIT", 50)

# Polling cadence while waiting for a Phantom container to finish.
LINKEDIN_POLL_INTERVAL_SECONDS = _int_env("LINKEDIN_POLL_INTERVAL_SECONDS", 10)

# Hard ceiling for the polling loop  -  give up after this many seconds and
# mark the queue rows as `error` for retry on the next tick.
LINKEDIN_POLL_TIMEOUT_SECONDS = _int_env("LINKEDIN_POLL_TIMEOUT_SECONDS", 300)

# ── LinkedIn worker thread (Phase 2 / P2-3  -  deep audit CRITICAL-1) ───────
# How often the LinkedIn worker thread wakes to drain the queue. Independent
# from the orchestration scheduler interval  -  the worker runs in its own
# thread so a long Phantom poll never blocks the email scheduler tick.
LINKEDIN_WORKER_INTERVAL_SECONDS = _int_env("LINKEDIN_WORKER_INTERVAL_SECONDS", 30)

# At worker startup, any LinkedInQueue row stuck in `processing` longer than
# this many minutes is presumed orphaned (e.g. systemd killed the previous
# process mid-poll) and reset to `queued` so it can be retried.
LINKEDIN_WORKER_RECONCILE_MINUTES = _int_env("LINKEDIN_WORKER_RECONCILE_MINUTES", 30)
# Minimum seconds between PB re-launches for unmatched leads.
LINKEDIN_RELAUNCH_COOLDOWN_SECONDS = _int_env("LINKEDIN_RELAUNCH_COOLDOWN_SECONDS", 3600)

# ── LinkedIn timeout and retry tuning ──────────────────────────────────────
# Configurable timeouts for poll_pb_results: how long to wait before timing
# out a processing item that never appears in PB results. Previously hardcoded
# to 48h (connect) and 24h (message).
LINKEDIN_CONNECT_TIMEOUT_HOURS = _int_env("LINKEDIN_CONNECT_TIMEOUT_HOURS", 48)
LINKEDIN_MESSAGE_TIMEOUT_HOURS = _int_env("LINKEDIN_MESSAGE_TIMEOUT_HOURS", 24)
# Maximum number of auto-retries before permanently failing a queue item.
# Previously hardcoded to 1 (retry_count < 1). Set higher if PB phantom
# scheduling is unreliable.
LINKEDIN_MAX_AUTO_RETRIES = _int_env("LINKEDIN_MAX_AUTO_RETRIES", 2)

# ── Engagement event ingestion (P1-7) ─────────────────────────────────────────
# Suppress duplicate opens/clicks that arrive within this window (seconds).
# Same logical knob the watchdog and ingestion module both reference.
SESSION_DEDUP_WINDOW_SECONDS = _int_env("SESSION_DEDUP_WINDOW_SECONDS", 300)

# ── Watchdog thresholds (P1-7 / deep audit LOW-1) ─────────────────────────────
# A scheduler tick that started this many minutes ago and never completed
# is considered stuck and triggers a Slack alert.
WATCHDOG_STUCK_JOB_MINUTES = _int_env("WATCHDOG_STUCK_JOB_MINUTES", 30)

# If `scheduler_last_run` is older than this many minutes, the scheduler is
# considered stale (probably crashed).
WATCHDOG_SCHEDULER_STALE_MINUTES = _int_env("WATCHDOG_SCHEDULER_STALE_MINUTES", 10)

# Sliding window over which the watchdog counts ERROR-level log lines.
WATCHDOG_ERROR_WINDOW_MINUTES = _int_env("WATCHDOG_ERROR_WINDOW_MINUTES", 15)

# Number of ERROR lines within the window that trips the alert.
WATCHDOG_ERROR_THRESHOLD = _int_env("WATCHDOG_ERROR_THRESHOLD", 10)

# Disk-usage thresholds (percent of /opt/outreach-system, or whatever
# WATCHDOG_DISK_PATH points to).
WATCHDOG_DISK_WARN_PCT = _int_env("WATCHDOG_DISK_WARN_PCT", 90)
WATCHDOG_DISK_CRIT_PCT = _int_env("WATCHDOG_DISK_CRIT_PCT", 95)

# Default working-days mask for the campaign sending window, as ISO weekday
# numbers (1=Mon … 7=Sun). Used by watchdog._is_in_sending_window when no
# per-user/global `working_days_mask` setting is present. Mon–Fri by default.
DEFAULT_WORKING_DAYS_MASK = (1, 2, 3, 4, 5)
