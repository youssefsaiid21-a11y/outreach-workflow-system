"""
Outreach System Watchdog - 24/7 monitoring with Slack alerts.

Runs as a separate systemd service. Checks:
  - Service liveness (HTTP health endpoint)
  - Stuck background jobs (running too long without progress)
  - Scheduler health (last run timestamp freshness)
  - Error rate spikes (too many ERROR logs in a window)
  - Disk usage (warn at 90%, critical at 95%)
  - Job completions (notify on finish with summary)

Usage:
  python watchdog.py

Env vars (from .env or environment):
  DATABASE_URL                  - PostgreSQL connection string
  WATCHDOG_SLACK_WEBHOOK_URL    - Slack incoming webhook URL
  WATCHDOG_INTERVAL             - Seconds between check cycles (default: 60)
  WATCHDOG_APP_URL              - Base URL of the app (default: http://127.0.0.1:3004)
"""

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ── Load .env ────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

# ── Config ───────────────────────────────────────────────────────────────────
INTERVAL = int(os.getenv("WATCHDOG_INTERVAL", "60"))
SLACK_WEBHOOK = os.getenv("WATCHDOG_SLACK_WEBHOOK_URL", "")
APP_URL = os.getenv("WATCHDOG_APP_URL", "http://127.0.0.1:3004")
DATABASE_URL = os.getenv("DATABASE_URL", "")
LOG_FILE = Path(__file__).parent / "logs" / "outreach.jsonl"
STATE_FILE = Path(__file__).parent / "watchdog_state.json"

# Thresholds - P1-7 / deep audit LOW-1: env-var overridable via src/config.py.
# Re-exported under the original names so existing references in this file
# (and any external grep) keep working unchanged.
from zoneinfo import ZoneInfo

from src.config import (  # noqa: E402
    WATCHDOG_STUCK_JOB_MINUTES as STUCK_JOB_MINUTES,
    WATCHDOG_SCHEDULER_STALE_MINUTES as SCHEDULER_STALE_MINUTES,
    WATCHDOG_ERROR_WINDOW_MINUTES as ERROR_WINDOW_MINUTES,
    WATCHDOG_ERROR_THRESHOLD as ERROR_THRESHOLD,
    WATCHDOG_DISK_WARN_PCT as DISK_WARN_PCT,
    WATCHDOG_DISK_CRIT_PCT as DISK_CRIT_PCT,
    DEFAULT_WORKING_DAYS_MASK,
)

# Timezone alias map (Instantly legacy names → IANA). Kept as empty dict
# now that Instantly is removed; any proper IANA name passes through as-is.
INSTANTLY_TZ_MAP: dict[str, str] = {}

# Cooldowns (seconds) - how long before the same alert can fire again
COOLDOWNS = {
    "service_down": 300,
    "stuck_job": 1800,
    "scheduler_stale": 1800,
    "error_spike": 900,
    "disk_warning": 3600,
    "disk_critical": 900,
    # LinkedIn outreach stuck: 2h cooldown per alert.
    "linkedin_stuck": 7200,
    # Paused orch states: 1h cooldown per campaign.
    "paused_orch": 3600,
    # LinkedIn queue failures: 1h cooldown.
    "linkedin_failures": 3600,
    # PhantomBuster phantom dead: 6h cooldown per agent.
    "phantom_dead": 21600,
    # New response: no cooldown suppression — each response notifies once.
    "new_response": 0,
}

# LinkedIn outreach: alert if items are stuck in processing for this long
# with zero new completions. PB processes ~20/day so 6 hours without any
# progress likely means the phantom or CSV feed is broken.
LINKEDIN_STUCK_MINUTES = int(os.getenv("WATCHDOG_LINKEDIN_STUCK_MINUTES", "360"))

# PhantomBuster staleness: how many hours since last run before alerting.
# PB phantoms run once daily, so 36h gives a full day + 12h buffer for
# schedule drift and timezone edge cases. Override via env var.
PHANTOM_STALE_HOURS = int(os.getenv("WATCHDOG_PHANTOM_STALE_HOURS", "36"))


# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("watchdog")

# ── Cooldown Tracker ─────────────────────────────────────────────────────────

class CooldownTracker:
    def __init__(self, path: Path):
        self._path = path
        self._cooldowns: dict[str, str] = {}  # alert_key -> ISO timestamp
        self._notified_jobs: list[str] = []
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._cooldowns = data.get("cooldowns", {})
                self._notified_jobs = data.get("notified_jobs", [])
            except Exception:
                self._cooldowns = {}
                self._notified_jobs = []

    def _save(self):
        # Keep only last 500 notified job IDs to avoid unbounded growth
        self._notified_jobs = self._notified_jobs[-500:]
        self._path.write_text(
            json.dumps({"cooldowns": self._cooldowns, "notified_jobs": self._notified_jobs}),
            encoding="utf-8",
        )
        try:
            os.chmod(str(self._path), 0o600)
        except OSError:
            pass

    def should_fire(self, key: str, cooldown_sec: int) -> bool:
        last = self._cooldowns.get(key)
        if not last:
            return True
        try:
            last_dt = datetime.fromisoformat(last)
            return (datetime.now(timezone.utc) - last_dt).total_seconds() >= cooldown_sec
        except Exception:
            return True

    def mark_fired(self, key: str):
        self._cooldowns[key] = datetime.now(timezone.utc).isoformat()
        self._save()

    def is_job_notified(self, job_id: str) -> bool:
        return job_id in self._notified_jobs

    def mark_job_notified(self, job_id: str):
        if job_id not in self._notified_jobs:
            self._notified_jobs.append(job_id)
            self._save()


# ── Slack Notifier ───────────────────────────────────────────────────────────

def send_slack(title: str, message: str, severity: str = "warning"):
    if not SLACK_WEBHOOK:
        log.warning("No WATCHDOG_SLACK_WEBHOOK_URL configured - skipping alert")
        return
    color = {"critical": "#FF0000", "warning": "#FFA500", "info": "#36A64F"}.get(severity, "#808080")
    payload = {
        "attachments": [{
            "color": color,
            "title": f"[Outreach] {title}",
            "text": message,
            "ts": int(time.time()),
        }]
    }
    try:
        r = httpx.post(SLACK_WEBHOOK, json=payload, timeout=10)
        if r.status_code != 200:
            log.warning("Slack webhook returned %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("Slack send failed: %s", e)


# ── Check Functions ──────────────────────────────────────────────────────────

def check_liveness() -> dict:
    """HTTP health check against the app."""
    try:
        r = httpx.get(f"{APP_URL}/api/health/", timeout=10)
        return {"ok": r.status_code == 200, "status": r.status_code}
    except httpx.ConnectError:
        return {"ok": False, "status": "connection_refused"}
    except httpx.TimeoutException:
        return {"ok": False, "status": "timeout"}
    except Exception as e:
        return {"ok": False, "status": str(e)}


def check_stuck_jobs(conn) -> list[dict]:
    """Find jobs stuck in running/pending state for too long."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_JOB_MINUTES)
    rows = conn.execute(
        text("""
            SELECT id, type, progress, progress_detail, updated_at
            FROM jobs
            WHERE status IN ('pending', 'running')
              AND updated_at < :cutoff
        """),
        {"cutoff": cutoff},
    ).fetchall()
    return [
        {
            "id": r[0], "type": r[1], "progress": r[2],
            "detail": r[3], "updated_at": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]


def check_scheduler(conn) -> dict:
    """Check if the scheduler has run recently."""
    row = conn.execute(
        text("SELECT value FROM app_settings WHERE key = 'scheduler_last_run'")
    ).fetchone()
    if not row or not row[0]:
        return {"ok": False, "reason": "scheduler_last_run not found in DB"}
    try:
        last_run = datetime.fromisoformat(row[0])
        age_min = (datetime.now(timezone.utc) - last_run).total_seconds() / 60
        return {"ok": age_min <= SCHEDULER_STALE_MINUTES, "last_run": row[0], "age_minutes": round(age_min, 1)}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def check_error_rate() -> dict:
    """Count ERROR-level log entries in the recent window."""
    if not LOG_FILE.exists():
        return {"ok": True, "errors": 0, "reason": "log file not found"}
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ERROR_WINDOW_MINUTES)
    error_count = 0
    latest_error = None
    try:
        # Read last 100KB of the log file to avoid reading the entire thing
        size = LOG_FILE.stat().st_size
        read_bytes = min(size, 100_000)
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            if size > read_bytes:
                f.seek(size - read_bytes)
                f.readline()  # skip partial first line
            for line in f:
                try:
                    entry = json.loads(line.strip())
                except Exception:
                    continue
                if entry.get("level") != "ERROR":
                    continue
                ts_str = entry.get("ts", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts >= cutoff:
                        error_count += 1
                        latest_error = entry.get("msg", "")[:200]
                except Exception:
                    pass
    except Exception as e:
        return {"ok": True, "errors": 0, "reason": f"log read error: {e}"}
    return {
        "ok": error_count <= ERROR_THRESHOLD,
        "errors": error_count,
        "latest": latest_error,
    }


def check_disk() -> dict:
    """Check disk usage."""
    usage = shutil.disk_usage("/")
    pct = round(usage.used / usage.total * 100, 1)
    severity = "critical" if pct >= DISK_CRIT_PCT else "warning" if pct >= DISK_WARN_PCT else "ok"
    return {"ok": severity == "ok", "percent": pct, "severity": severity}


def _read_setting(conn, key: str, user_id: str = None, default: str = None) -> str:
    """Read from app_settings, user-specific key first, then global."""
    if user_id:
        user_key = f"__user__{user_id}__{key}"
        row = conn.execute(
            text("SELECT value FROM app_settings WHERE key = :k"),
            {"k": user_key},
        ).fetchone()
        if row and row[0]:
            return row[0]
    row = conn.execute(
        text("SELECT value FROM app_settings WHERE key = :k"),
        {"k": key},
    ).fetchone()
    return row[0] if row and row[0] else default


def _is_in_sending_window(conn, user_id: str) -> bool:
    """Check if the current time falls within the campaign's sending window.

    Reads sending_timezone, sending_window_start/end, and working_days_mask
    from app_settings (user-specific first, then global fallback).
    Returns True if now is inside the sending window, False otherwise.
    """
    tz_name = _read_setting(conn, "sending_timezone", user_id, "Etc/GMT-8")
    tz_name = INSTANTLY_TZ_MAP.get(tz_name, tz_name)
    window_start = _read_setting(conn, "sending_window_start", user_id, "08:00")
    window_end = _read_setting(conn, "sending_window_end", user_id, "17:00")

    mask_raw = _read_setting(conn, "working_days_mask", user_id)
    if mask_raw:
        try:
            working_days = json.loads(mask_raw)
        except (json.JSONDecodeError, TypeError):
            working_days = list(DEFAULT_WORKING_DAYS_MASK)
    else:
        working_days = list(DEFAULT_WORKING_DAYS_MASK)

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        log.warning("_is_in_sending_window: invalid timezone '%s', defaulting to UTC", tz_name)
        tz = ZoneInfo("UTC")

    now_local = datetime.now(tz)

    # ISO weekday: 1=Mon, 7=Sun
    if now_local.isoweekday() not in working_days:
        return False

    try:
        start_h, start_m = map(int, window_start.split(":"))
        end_h, end_m = map(int, window_end.split(":"))
    except Exception:
        start_h, start_m = 8, 0
        end_h, end_m = 17, 0

    current_minutes = now_local.hour * 60 + now_local.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m

    return start_minutes <= current_minutes < end_minutes


def check_linkedin_outreach(conn, tracker: CooldownTracker) -> dict:
    """Check if LinkedIn outreach is stuck.

    Compares the number of 'done' items now vs. last check. If there are
    items in 'processing' and zero new completions for LINKEDIN_STUCK_MINUTES,
    something is wrong (PB not running, CSV feed broken, etc.).

    Returns a dict with status info; the caller decides whether to alert.
    """
    now = datetime.now(timezone.utc)
    state = tracker._cooldowns

    row = conn.execute(text(
        "SELECT "
        "  COUNT(*) FILTER (WHERE status = 'processing') AS processing, "
        "  COUNT(*) FILTER (WHERE status = 'done') AS done, "
        "  COUNT(*) FILTER (WHERE status = 'failed') AS failed "
        "FROM linkedin_queue"
    )).fetchone()

    processing = row[0] if row else 0
    done_count = row[1] if row else 0
    failed = row[2] if row else 0

    result = {
        "processing": processing,
        "done": done_count,
        "failed": failed,
        "stuck": False,
    }

    if processing == 0:
        return result

    # Compare done_count to previous watermark
    state_key = "linkedin_done_watermark"
    prev_blob = state.get(state_key)
    prev_done = -1
    prev_ts = now
    if isinstance(prev_blob, str) and "|" in prev_blob:
        try:
            prev_done_str, prev_ts_str = prev_blob.split("|", 1)
            prev_done = int(prev_done_str)
            prev_ts = datetime.fromisoformat(prev_ts_str)
        except Exception:
            prev_done = -1
            prev_ts = now

    if done_count > prev_done:
        state[state_key] = f"{done_count}|{now.isoformat()}"
        tracker._save()
        return result

    stale_minutes = (now - prev_ts).total_seconds() / 60
    if stale_minutes >= LINKEDIN_STUCK_MINUTES:
        result["stuck"] = True
        result["stale_minutes"] = int(stale_minutes)

    return result


def _has_overdue_linkedin_actions(conn, step_types: list[str]) -> bool:
    """Check if there are orch states with overdue LinkedIn steps.

    Only returns True if there are leads whose scheduled LinkedIn action
    is past due (scheduled_next_at < now) AND at a matching step type.
    If nothing is overdue, the phantom being idle is expected behavior.
    """
    try:
        row = conn.execute(text("""
            SELECT COUNT(*) AS cnt
            FROM orch_sequence_states oss
            WHERE oss.status = 'waiting_for_next_step'
              AND oss.scheduled_next_at < NOW()
              AND EXISTS (
                  SELECT 1 FROM sequence_configs sc
                  WHERE sc.config_json::jsonb -> 'steps' IS NOT NULL
                    AND (sc.config_json::jsonb -> 'sequence_id')::text = concat('"', oss.sequence_id, '"')
                    AND (sc.config_json::jsonb -> 'steps' -> oss.current_step_index ->> 'step_type')
                        = ANY(:step_types)
              )
        """), {"step_types": step_types}).fetchone()
        return (row.cnt or 0) > 0
    except Exception as exc:
        log.debug("_has_overdue_linkedin_actions query failed: %s", exc)
        # Fall back to simple check: any overdue waiting states at all
        try:
            row = conn.execute(text("""
                SELECT COUNT(*) AS cnt FROM orch_sequence_states
                WHERE status = 'waiting_for_next_step'
                  AND scheduled_next_at < NOW() - INTERVAL '1 hour'
            """)).fetchone()
            return (row.cnt or 0) > 0
        except Exception:
            return False  # can't confirm the problem — don't alert on DB failure


def check_phantom_health(tracker: CooldownTracker, conn=None) -> list[dict]:
    """Check if PhantomBuster phantoms have run recently.

    Verifies that the Connect and Message Sender phantoms have had a
    successful run recently. Only alerts if there are actually overdue
    LinkedIn actions that the phantom should be processing — a phantom
    being idle when nothing is due is expected, not an error.
    """
    import os
    import httpx

    api_key = os.getenv("PHANTOMBUSTER_API_KEY", "")
    if not api_key:
        return []

    headers = {"X-Phantombuster-Key": api_key}
    agents = {
        "connect": os.getenv("PHANTOMBUSTER_CONNECT_AGENT_ID", ""),
        "message": os.getenv("PHANTOMBUSTER_MESSAGE_AGENT_ID", ""),
    }
    # Map phantom type → orch step types it services
    _STEP_TYPES = {
        "connect": ["send_linkedin_connect"],
        "message": ["send_linkedin_message"],
    }

    problems = []
    now = datetime.now(timezone.utc)

    for name, agent_id in agents.items():
        if not agent_id:
            continue
        try:
            resp = httpx.get(
                "https://api.phantombuster.com/api/v2/agents/fetch-output",
                params={"id": agent_id},
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                problems.append({"agent": name, "issue": f"API error {resp.status_code}"})
                continue

            data = resp.json()
            exit_code = data.get("exitCode")
            container_id = data.get("containerId")

            # Check last run time via container
            if container_id:
                cr = httpx.get(
                    "https://api.phantombuster.com/api/v2/containers/fetch",
                    params={"id": container_id},
                    headers=headers,
                    timeout=15,
                )
                if cr.status_code == 200:
                    c_data = cr.json()
                    ended_at = c_data.get("endedAt")
                    if ended_at:
                        ended_dt = datetime.fromtimestamp(ended_at / 1000, tz=timezone.utc)
                        hours_ago = (now - ended_dt).total_seconds() / 3600
                        if hours_ago > PHANTOM_STALE_HOURS:
                            # Only alert if there are overdue actions for this phantom type
                            if conn and not _has_overdue_linkedin_actions(conn, _STEP_TYPES.get(name, [])):
                                log.info(
                                    "phantom %s idle for %dh but no overdue %s actions — skipping alert",
                                    name, int(hours_ago), name,
                                )
                                continue
                            problems.append({
                                "agent": name,
                                "issue": f"last run {int(hours_ago)}h ago (threshold: {PHANTOM_STALE_HOURS}h)",
                                "exit_code": exit_code,
                            })
                            continue

            # Check exit code of last run
            if exit_code is not None and exit_code != 0:
                problems.append({
                    "agent": name,
                    "issue": f"last run failed (exit_code={exit_code})",
                })

        except Exception as exc:
            log.warning("phantom health check for %s failed: %s", name, exc)

    return problems


def check_paused_orch_states(conn, tracker: CooldownTracker) -> dict:
    """Detect new orchestration states that have been paused.

    Paused states indicate LinkedIn failures (connect or message) that
    halted lead progression. Alerts per-campaign so the operator can
    investigate and unpause if appropriate.
    """
    state = tracker._cooldowns
    row = conn.execute(text(
        "SELECT c.name, COUNT(*) AS cnt "
        "FROM orch_sequence_states o "
        "JOIN campaigns c ON c.id = ("
        "  SELECT es.campaign_id FROM email_sequences es "
        "  WHERE es.prospect_id = o.lead_id LIMIT 1"
        ") "
        "WHERE o.status = 'paused' "
        "GROUP BY c.name"
    )).fetchall()

    total = sum(r[1] for r in row) if row else 0
    result = {"total_paused": total, "campaigns": [], "new_pauses": False}

    watermark_key = "paused_orch_watermark"
    prev_total = int(state.get(watermark_key, "0") or "0")

    if total > prev_total:
        result["new_pauses"] = True
        result["campaigns"] = [{"name": r[0], "count": r[1]} for r in row]

    state[watermark_key] = str(total)
    tracker._save()
    return result


def check_linkedin_failures(conn, tracker: CooldownTracker) -> dict:
    """Detect new LinkedIn queue failures in the last 24 hours.

    Tracks a watermark of total failed items. When new failures appear,
    returns details so the caller can alert.
    """
    state = tracker._cooldowns
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    row = conn.execute(text(
        "SELECT COUNT(*) FROM linkedin_queue "
        "WHERE status = 'failed' AND created_at >= :since"
    ), {"since": since}).fetchone()

    recent_failures = row[0] if row else 0

    watermark_key = "linkedin_failures_watermark"
    prev = int(state.get(watermark_key, "0") or "0")

    result = {"recent_failures": recent_failures, "new_failures": False}
    if recent_failures > prev:
        result["new_failures"] = True
        result["delta"] = recent_failures - prev

    state[watermark_key] = str(recent_failures)
    tracker._save()
    return result


def check_new_responses(conn, tracker: CooldownTracker) -> list[dict]:
    """Detect new genuine email replies (not bounces or auto-replies).

    Tracks the count of prospects with status='replied'. Bounced prospects
    have status='bounced' so they are excluded automatically.
    """
    state = tracker._cooldowns
    notifications = []

    replied_row = conn.execute(text(
        "SELECT COUNT(*) FROM prospects WHERE status = 'replied'"
    )).fetchone()
    replied_count = replied_row[0] if replied_row else 0

    prev_replied = int(state.get("replied_watermark", 0))
    if replied_count > prev_replied:
        new_replies = conn.execute(text(
            "SELECT first_name, last_name, company, email FROM prospects "
            "WHERE status = 'replied' ORDER BY created_at DESC LIMIT :n"
        ), {"n": replied_count - prev_replied}).fetchall()

        for r in new_replies:
            notifications.append({
                "type": "email_reply",
                "name": f"{r[0] or ''} {r[1] or ''}".strip(),
                "company": r[2] or "",
                "email": r[3] or "",
            })
        state["replied_watermark"] = str(replied_count)
        tracker._save()

    return notifications


def check_job_completions(conn, tracker: CooldownTracker) -> list[dict]:
    """Find recently completed jobs that haven't been notified yet."""
    # Look at jobs completed in the last hour
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    rows = conn.execute(
        text("""
            SELECT id, type, status, result, error, progress_detail, updated_at
            FROM jobs
            WHERE status IN ('done', 'completed_with_errors', 'failed')
              AND updated_at > :cutoff
        """),
        {"cutoff": cutoff},
    ).fetchall()
    new_completions = []
    for r in rows:
        job_id = r[0]
        if tracker.is_job_notified(job_id):
            continue
        result = r[3]
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                pass
        new_completions.append({
            "id": job_id, "type": r[1], "status": r[2],
            "result": result, "error": r[4], "detail": r[5],
            "updated_at": r[6].isoformat() if r[6] else None,
        })
    return new_completions


# ── Main Loop ────────────────────────────────────────────────────────────────

def run_cycle(engine, tracker: CooldownTracker) -> dict:
    """Run all checks once and fire alerts. Returns results dict."""
    now = datetime.now(timezone.utc).isoformat()
    results = {"checked_at": now, "alerts_fired": []}

    # 1. Liveness
    liveness = check_liveness()
    results["liveness"] = liveness
    if not liveness["ok"]:
        key = "service_down"
        if tracker.should_fire(key, COOLDOWNS[key]):
            send_slack(
                "Service Unreachable",
                f"HTTP health check failed. Status: {liveness['status']}.\n"
                "systemd should auto-restart within 5s.",
                "critical",
            )
            tracker.mark_fired(key)
            results["alerts_fired"].append(key)

    # 2-5: DB-dependent checks (skip if service is down and DB might be affected)
    try:
        with engine.connect() as conn:
            # 2. Stuck jobs
            stuck = check_stuck_jobs(conn)
            results["stuck_jobs"] = stuck
            for job in stuck:
                key = f"stuck_job:{job['id']}"
                if tracker.should_fire(key, COOLDOWNS["stuck_job"]):
                    age = ""
                    if job["updated_at"]:
                        try:
                            mins = (datetime.now(timezone.utc) - datetime.fromisoformat(job["updated_at"])).total_seconds() / 60
                            age = f" running for {int(mins)}min,"
                        except Exception:
                            pass
                    send_slack(
                        "Stuck Job Detected",
                        f"Job `{job['id'][:8]}` ({job['type']}){age} "
                        f"last progress: {job['progress']}% - \"{job['detail'] or 'no detail'}\".\n"
                        "Consider checking logs or cancelling.",
                        "warning",
                    )
                    tracker.mark_fired(key)
                    results["alerts_fired"].append(key)

            # 3. Scheduler
            sched = check_scheduler(conn)
            results["scheduler"] = sched
            if not sched["ok"]:
                key = "scheduler_stale"
                if tracker.should_fire(key, COOLDOWNS[key]):
                    send_slack(
                        "Scheduler Stale",
                        f"Scheduler hasn't run in {sched.get('age_minutes', '?')} minutes "
                        f"(threshold: {SCHEDULER_STALE_MINUTES}min).\n"
                        f"Reason: {sched.get('reason', 'last_run too old')}",
                        "warning",
                    )
                    tracker.mark_fired(key)
                    results["alerts_fired"].append(key)

            # 3.6. LinkedIn outreach health
            try:
                li_status = check_linkedin_outreach(conn, tracker)
                results["linkedin"] = li_status
                if li_status.get("stuck"):
                    key = "linkedin_stuck"
                    if tracker.should_fire(key, COOLDOWNS[key]):
                        send_slack(
                            "LinkedIn Outreach Stuck",
                            f"{li_status['processing']} LinkedIn connection(s) in processing "
                            f"with no new completions for {li_status['stale_minutes']}min "
                            f"(threshold: {LINKEDIN_STUCK_MINUTES}min).\n"
                            f"Done so far: {li_status['done']} | Failed: {li_status['failed']}\n"
                            f"Check PhantomBuster agent status and CSV feed.",
                            "warning",
                        )
                        tracker.mark_fired(key)
                        results["alerts_fired"].append(key)
            except Exception as e:
                log.warning("linkedin outreach check crashed: %s", e)

            # 3.7a. Paused orchestration states (LinkedIn failures)
            try:
                paused_info = check_paused_orch_states(conn, tracker)
                results["paused_orch"] = paused_info
                if paused_info.get("new_pauses"):
                    for camp in paused_info["campaigns"]:
                        key = f"paused_orch:{camp['name']}"
                        if tracker.should_fire(key, COOLDOWNS["paused_orch"]):
                            send_slack(
                                "Orch States Paused - LinkedIn Failure",
                                f"*{camp['name']}*: {camp['count']} lead(s) paused due to "
                                f"LinkedIn step failure.\n"
                                f"Total paused across all campaigns: {paused_info['total_paused']}.\n"
                                f"Check `orch_sequence_states` for details.",
                                "warning",
                            )
                            tracker.mark_fired(key)
                            results["alerts_fired"].append(key)
            except Exception as e:
                log.warning("paused orch check crashed: %s", e)

            # 3.7b. LinkedIn queue failures (new failures in last 24h)
            try:
                li_failures = check_linkedin_failures(conn, tracker)
                results["linkedin_failures"] = li_failures
                if li_failures.get("new_failures"):
                    key = "linkedin_failures"
                    if tracker.should_fire(key, COOLDOWNS[key]):
                        send_slack(
                            "LinkedIn Queue Failures Detected",
                            f"{li_failures['delta']} new LinkedIn queue failure(s) in the last 24h.\n"
                            f"Total recent failures: {li_failures['recent_failures']}.\n"
                            f"Common cause: PB timeout (no_result_after_Xh) or profile unavailable.",
                            "warning",
                        )
                        tracker.mark_fired(key)
                        results["alerts_fired"].append(key)
            except Exception as e:
                log.warning("linkedin failures check crashed: %s", e)

            # 3.7. New email replies (genuine replies only, not bounces)
            try:
                responses = check_new_responses(conn, tracker)
                results["new_responses"] = len(responses)
                for resp in responses:
                    send_slack(
                        "New Email Reply",
                        f"*{resp['name']}* from {resp['company']} replied to your outreach.\n"
                        f"Email: {resp['email']}",
                        "good",
                    )
                    results["alerts_fired"].append(f"new_response:reply:{resp['email']}")
            except Exception as e:
                log.warning("new responses check crashed: %s", e)

            # 4. Job completions
            completions = check_job_completions(conn, tracker)
            results["recent_completions"] = len(completions)
            for job in completions:
                r = job.get("result") or {}
                generated = r.get("generated", "?")
                errors = r.get("errors", 0)
                tok_in = r.get("tokens_input", 0)
                tok_out = r.get("tokens_output", 0)

                if job["status"] == "failed":
                    title = "Job Failed"
                    severity = "critical"
                    msg = (
                        f"Job `{job['id'][:8]}` ({job['type']}) failed.\n"
                        f"Error: {job.get('error', 'unknown')}"
                    )
                elif job["status"] == "completed_with_errors":
                    title = "Job Completed with Errors"
                    severity = "warning"
                    failed_sample = ", ".join((r.get("failed_emails") or [])[:3])
                    more = f" +{len(r.get('failed_emails', [])) - 3} more" if len(r.get("failed_emails", [])) > 3 else ""
                    msg = (
                        f"Job `{job['id'][:8]}` ({job['type']}): "
                        f"{generated} generated, {errors} errors.\n"
                        f"Failed: {failed_sample}{more}\n"
                        f"Tokens: {tok_in:,} in / {tok_out:,} out"
                    )
                else:
                    title = "Job Completed"
                    severity = "info"
                    msg = (
                        f"Job `{job['id'][:8]}` ({job['type']}): "
                        f"{generated} generated, {errors} errors.\n"
                        f"Tokens: {tok_in:,} in / {tok_out:,} out"
                    )

                send_slack(title, msg, severity)
                tracker.mark_job_notified(job["id"])
                results["alerts_fired"].append(f"job_done:{job['id'][:8]}")

    except Exception as e:
        log.warning("DB checks failed: %s", e)
        results["db_error"] = str(e)

    # 5. Error rate (log-file based, no DB needed)
    err_rate = check_error_rate()
    results["error_rate"] = err_rate
    if not err_rate["ok"]:
        key = "error_spike"
        if tracker.should_fire(key, COOLDOWNS[key]):
            send_slack(
                "Error Rate Spike",
                f"{err_rate['errors']} errors in last {ERROR_WINDOW_MINUTES} min "
                f"(threshold: {ERROR_THRESHOLD}).\n"
                f"Latest: \"{err_rate.get('latest', '?')}\"",
                "warning",
            )
            tracker.mark_fired(key)
            results["alerts_fired"].append(key)

    # 5b. PhantomBuster phantom health (API + DB to check overdue actions)
    _ph_conn = None
    try:
        _ph_conn = engine.connect() if engine else None
        phantom_problems = check_phantom_health(tracker, conn=_ph_conn)
        results["phantom_health"] = phantom_problems
        for prob in phantom_problems:
            key = f"phantom_dead:{prob['agent']}"
            if tracker.should_fire(key, COOLDOWNS["phantom_dead"]):
                send_slack(
                    f"PhantomBuster Phantom Down: {prob['agent']}",
                    f"The *{prob['agent']}* phantom is not running properly.\n"
                    f"Issue: {prob['issue']}\n"
                    f"LinkedIn {prob['agent']} actions will not be processed until this is resolved.",
                    "critical",
                )
                tracker.mark_fired(key)
                results["alerts_fired"].append(key)
    except Exception as e:
        log.warning("phantom health check crashed: %s", e)
    finally:
        if _ph_conn is not None:
            try:
                _ph_conn.close()
            except Exception:
                pass

    # 6. Disk
    disk = check_disk()
    results["disk"] = disk
    if not disk["ok"]:
        key = f"disk_{disk['severity']}"
        cooldown = COOLDOWNS.get(key, 3600)
        if tracker.should_fire(key, cooldown):
            send_slack(
                f"Disk Usage {disk['severity'].title()}",
                f"Disk is {disk['percent']}% full.",
                disk["severity"],
            )
            tracker.mark_fired(key)
            results["alerts_fired"].append(key)

    return results


def write_state(results: dict):
    """Write last check results for the /api/health/watchdog endpoint."""
    try:
        STATE_FILE.write_text(json.dumps(results, default=str), encoding="utf-8")
    except Exception as e:
        log.warning("Failed to write state file: %s", e)


def main():
    log.info("Watchdog starting - interval=%ds, app=%s", INTERVAL, APP_URL)
    if not SLACK_WEBHOOK:
        log.warning("WATCHDOG_SLACK_WEBHOOK_URL not set - alerts will only be logged")
    if not DATABASE_URL:
        log.error("DATABASE_URL not set - DB checks will fail")

    engine = create_engine(DATABASE_URL) if DATABASE_URL else None
    tracker = CooldownTracker(Path(__file__).parent / "watchdog_cooldowns.json")

    while True:
        try:
            results = run_cycle(engine, tracker)
            write_state(results)
            fired = results.get("alerts_fired", [])
            if fired:
                log.info("Cycle done - alerts fired: %s", fired)
            else:
                log.info("Cycle done - all clear")
        except Exception as e:
            log.exception("Watchdog cycle crashed: %s", e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
