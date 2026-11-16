"""
Database connection setup, session factory, and runtime migrations.

This module:
  1. Creates the SQLAlchemy engine (PostgreSQL in prod, SQLite for local dev/tests)
  2. Provides SessionLocal (session factory) and get_db (FastAPI dependency)
  3. Runs init_db() at startup to create tables and apply hardcoded migrations
  4. Backfills legacy NULL user_id rows to the admin user (multi-tenant safety)

Two migration systems coexist:
  - Alembic (alembic/versions/) — the canonical migration path for production.
    Run via `alembic upgrade head` on the VPS after deploy.
  - Hardcoded migration list (_NEW_COLUMNS, _NEW_INDEXES) — a belt-and-suspenders
    fallback that runs on every startup. Handles columns/indexes that were added
    between Alembic runs or on dev SQLite databases that were never alembic-stamped.
    Safe to run repeatedly: "duplicate column" / "already exists" errors are caught.

Environment variables:
  - DATABASE_URL: full connection string. Defaults to sqlite:///outreach.db for local dev.
    Production uses postgresql://postgres:...@127.0.0.1:5432/outreach
  - OUTREACH_BACKFILL_ALLOW_RECENT: set to "1" to bypass the multi-rep safety check
    in the NULL user_id backfill (one-time manual recovery only)

Related files:
  - src/db/models.py         — ORM model definitions
  - alembic/env.py           — Alembic migration environment
  - main.py                  — calls init_db() at startup
"""

import os
from pathlib import Path
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

# Use absolute path so the DB location is stable regardless of cwd.
# In production, DATABASE_URL is set in .env to the PostgreSQL connection string.
_HERE = Path(__file__).resolve().parent.parent.parent  # outreach-system/
_DEFAULT_DB = f"sqlite:///{_HERE / 'outreach.db'}"
DATABASE_URL = os.getenv("DATABASE_URL", _DEFAULT_DB)

# SQLite needs check_same_thread=False because FastAPI serves requests on
# different threads than the one that created the connection. PostgreSQL
# handles this natively via its connection pool.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

if DATABASE_URL.startswith("postgresql"):
    # Production pool settings tuned for the single-process, multi-threaded architecture:
    # - pool_size=20: enough for background threads + concurrent HTTP requests
    # - max_overflow=30: burst capacity for launch operations that open many sessions
    # - pool_pre_ping=True: detect dead connections before use (prevents stale conn errors)
    # - pool_recycle=3600: recycle connections every hour (Postgres default timeout is 2h)
    # - pool_timeout=10: fail fast if pool is exhausted (vs. hanging indefinitely)
    engine = create_engine(
        DATABASE_URL,
        pool_size=20,
        max_overflow=30,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_timeout=10,
    )
else:
    # SQLite (local dev/tests) — no connection pooling needed
    engine = create_engine(DATABASE_URL, connect_args=connect_args)

# Enable WAL mode for SQLite  -  prevents "database is locked" errors when
# background threads write while HTTP request handlers read concurrently.
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_wal_mode(conn, _):
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, faster than FULL
        cursor.execute("PRAGMA foreign_keys=ON")      # enforce FK cascades
        cursor.close()

# Session factory — every DB operation creates a session via SessionLocal().
# autoflush=False prevents implicit flushes before queries (we commit explicitly).
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Declarative base — all ORM models inherit from this (see models.py).
Base = declarative_base()


def get_db():
    """FastAPI dependency that provides a DB session per request.
    The session is automatically closed when the request completes,
    even if an exception occurs. Usage: db: Session = Depends(get_db)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialise the database: create all tables and run migrations.

    Called once at startup from main.py. Safe to call multiple times —
    create_all() is a no-op for tables that already exist, and the migration
    runner catches "duplicate column" / "already exists" errors gracefully.
    """
    from . import models  # noqa: F401  -  registers models before create_all

    # Register orchestration ORM models (orch_sequence_states, orch_audit_logs)
    # so create_all() creates their tables. The try/except prevents import
    # errors from blocking server startup if the orchestration package has issues.
    try:
        from ..orchestration import orm as _orch_orm  # noqa: F401
    except ImportError:
        pass

    # Create all tables defined in ORM models (idempotent — skips existing tables)
    Base.metadata.create_all(bind=engine)
    # Apply hardcoded column/index additions for columns added between Alembic runs
    _run_migrations()
    print("Database ready.")


# ── Hardcoded migration list ──────────────────────────────────────────────────
# Each tuple is (table_name, column_name, column_type). These are applied via
# ALTER TABLE ADD COLUMN on every startup. "Duplicate column" errors are caught
# silently (meaning the column already exists — expected on subsequent startups).
#
# WHY this exists alongside Alembic: the system evolved rapidly, and some columns
# were added in code before Alembic migrations were written. This list ensures
# every environment (dev SQLite, prod Postgres, test databases) has all columns
# regardless of whether Alembic was run. The column names are hardcoded strings
# (not user input) so there's no SQL injection risk.
_NEW_COLUMNS: list[tuple[str, str, str]] = [
    ("prospects", "email_validation_status",     "VARCHAR"),
    ("prospects", "email_validation_sub_status", "VARCHAR"),
    ("prospects", "email_validated_at",          "DATETIME"),
    ("prospects", "segment_id",                  "VARCHAR"),
    ("prospects", "linkedin_headline",           "VARCHAR"),
    ("prospects", "linkedin_summary",            "TEXT"),
    ("prospects", "company_description",         "TEXT"),
    ("prospects", "enriched_at",                 "DATETIME"),
    ("campaigns", "meetings_booked",             "INTEGER DEFAULT 0"),
    ("campaigns", "positive_replies",            "INTEGER DEFAULT 0"),
    ("campaigns", "tokens_input",                "INTEGER DEFAULT 0"),
    ("campaigns", "tokens_output",               "INTEGER DEFAULT 0"),
    # Fix 21: LinkedIn connection tracking columns
    ("prospects", "linkedin_connected",          "BOOLEAN DEFAULT 0"),
    ("prospects", "linkedin_connect_sent_at",    "DATETIME"),
    # Explicit prospect selection per campaign
    ("campaigns", "prospect_ids",               "JSON"),
    # Multi-user: user_id on all core tables
    ("prospects",       "user_id",  "VARCHAR"),
    ("campaigns",       "user_id",  "VARCHAR"),
    ("jobs",            "user_id",  "VARCHAR"),
    ("credit_logs",     "user_id",  "VARCHAR"),
    ("send_logs",       "user_id",  "VARCHAR"),
    ("style_profiles",  "user_id",  "VARCHAR"),
    # A/B testing columns
    ("campaigns",       "ab_test_enabled",   "BOOLEAN DEFAULT 0"),
    ("email_sequences", "email1_subject_b",  "TEXT"),
    ("email_sequences", "variant_sent",      "VARCHAR"),
    # 30-day sequence: emails 4-6 + LinkedIn content
    ("email_sequences", "email4_subject",         "TEXT"),
    ("email_sequences", "email4_body",            "TEXT"),
    ("email_sequences", "email5_subject",         "TEXT"),
    ("email_sequences", "email5_body",            "TEXT"),
    ("email_sequences", "email6_subject",         "TEXT"),
    ("email_sequences", "email6_body",            "TEXT"),
    ("email_sequences", "linkedin_connect_note",  "TEXT"),
    ("email_sequences", "linkedin_message",       "TEXT"),
    # Engagement scoring on prospects
    ("prospects", "engagement_score", "INTEGER DEFAULT 0"),
    # Account-based grouping
    ("prospects", "account_id", "VARCHAR"),
    # SendLog timing: pending → sent lifecycle
    ("send_logs", "status",      "VARCHAR DEFAULT 'sent'"),    # default 'sent' for existing rows
    ("send_logs", "enrolled_at", "TIMESTAMP" if not DATABASE_URL.startswith("sqlite") else "DATETIME"),
    # Multi-user: conference tables
    ("conference_leads",   "user_id", "VARCHAR"),
    ("conference_inboxes", "user_id", "VARCHAR"),
    # Conference lead enrichment + sequence data
    ("conference_leads", "raw_data",               "TEXT"),
    ("conference_leads", "generated_sequence",      "TEXT"),
    ("conference_leads", "sequence_generated_at",   "TIMESTAMP" if not DATABASE_URL.startswith("sqlite") else "DATETIME"),
    ("conference_leads", "cc",                      "VARCHAR"),
    ("conference_leads", "category",                "VARCHAR"),
    # FIX-3: Conference token tracking columns referenced by code but missing
    # from the migration list  -  caused /conference/costs to crash on every
    # existing DB.
    ("conference_leads", "tokens_input",            "INTEGER DEFAULT 0"),
    ("conference_leads", "tokens_output",           "INTEGER DEFAULT 0"),
    # FIX-2: Per-user API key hashes (X-Api-Key impersonation hardening).
    # Format `<user_id>.<secret>`  -  column stores bcrypt hash of the secret.
    ("users", "api_key_hash", "VARCHAR"),
    # FIX-17: per-user app settings  -  nullable, NULL = global default row.
    ("app_settings", "user_id", "VARCHAR"),
    # FIX-32: per-user suppression entries  -  nullable, NULL = global entry.
    ("unsubscribes", "user_id", "VARCHAR"),
    # P2-2 / deep audit HIGH-4: failure retry ladder counter on orch states.
    # Alembic 0002 is the canonical migration; this entry covers any legacy
    # SQLite test DB / dev environment that hasn't been alembic-stamped.
    ("orch_sequence_states", "consecutive_failure_count", "INTEGER DEFAULT 0"),
    # P3-1: multi-tenancy on the orchestration + linkedin_queue tables.
    # Alembic 0003 is the canonical migration; these entries cover any
    # legacy SQLite test DB / dev environment that hasn't been alembic-
    # stamped.
    ("orch_sequence_states", "user_id", "VARCHAR"),
    ("linkedin_queue",       "user_id", "VARCHAR"),
    # Newsletter tables  -  runtime fallback for environments without alembic 0017.
    # The tables themselves are created by Base.metadata.create_all(); these
    # entries only cover additive column additions on already-existing tables,
    # which is not needed for net-new tables. Kept as documentation placeholders.
]


_NEW_INDEXES: list[tuple[str, str, str]] = [
    # (index_name, table, column)
    ("ix_prospects_status",                  "prospects",       "status"),
    ("ix_prospects_email_validation_status", "prospects",       "email_validation_status"),
    ("ix_email_sequences_status",            "email_sequences", "status"),
    ("ix_campaigns_status",                  "campaigns",       "status"),
    ("ix_jobs_type_status",                  "jobs",            "type, status"),
    # Multi-user indexes
    ("ix_prospects_user_id",                 "prospects",       "user_id"),
    ("ix_campaigns_user_id",                 "campaigns",       "user_id"),
    ("ix_jobs_user_id",                      "jobs",            "user_id"),
    ("ix_credit_logs_user_id",               "credit_logs",     "user_id"),
    ("ix_send_logs_user_id",                 "send_logs",       "user_id"),
    # Reply management indexes
    ("ix_replies_classification",            "replies",         "classification"),
    ("ix_replies_status",                    "replies",         "status"),
    ("ix_replies_received_at",               "replies",         "received_at"),
    # Engagement score index
    ("ix_prospects_engagement_score",        "prospects",       "engagement_score"),
    # Account-based grouping
    ("ix_prospects_account_id",              "prospects",       "account_id"),
    # Domain health snapshots
    ("ix_domain_health_snapshots_domain",    "domain_health_snapshots", "domain"),
    # LinkedIn queue
    ("ix_linkedin_queue_status",             "linkedin_queue",          "status"),
    ("ix_linkedin_queue_prospect_id",        "linkedin_queue",          "prospect_id"),
    # P3-1: per-user filtering on orch + linkedin tables
    ("ix_orch_seq_states_user_id",           "orch_sequence_states",    "user_id"),
    ("ix_linkedin_queue_user_id",            "linkedin_queue",          "user_id"),
    # Conference leads unique constraint
    ("ix_conf_leads_conf_email",             "conference_leads",        "conference_name, email"),
    # Multi-user: conference tables
    ("ix_conf_leads_user_id",                "conference_leads",        "user_id"),
    ("ix_conf_inboxes_user_id",              "conference_inboxes",      "user_id"),
    # Newsletter indexes (runtime fallback for alembic 0017)
    ("ix_newsletters_track",                 "newsletters",             "track"),
    ("ix_newsletters_status",                "newsletters",             "status"),
    ("ix_newsletters_owner_id",              "newsletters",             "owner_id"),
    ("ix_nl_recip_newsletter_id",            "newsletter_recipients",   "newsletter_id"),
    ("ix_nl_recip_contact_email",            "newsletter_recipients",   "contact_email"),
    ("ix_nl_recip_status",                   "newsletter_recipients",   "status"),
    ("ix_newsletter_unsubscribes_email",     "newsletter_unsubscribes", "email"),
]


# FIX-9: composite UNIQUE index for per-user prospect email enforcement.
# Stored separately because the standard non-unique CREATE INDEX path above
# can't express UNIQUE.
_NEW_UNIQUE_INDEXES: list[tuple[str, str, str]] = [
    ("ix_prospects_user_email", "prospects", "user_id, email"),
    ("ix_nl_recip_newsletter_email", "newsletter_recipients", "newsletter_id, contact_email"),
]


def _run_migrations():
    """Apply hardcoded column additions and index creations to existing tables.

    This is the belt-and-suspenders migration runner that complements Alembic.
    It runs on every startup and is fully idempotent — errors from pre-existing
    columns/indexes are caught and ignored. Only genuine failures (e.g. wrong
    table name, syntax error) are re-raised.
    """
    import logging
    _log = logging.getLogger(__name__)

    with engine.connect() as conn:
        for table, column, col_type in _NEW_COLUMNS:
            # Build the statement from validated, hardcoded strings only.
            stmt = text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            try:
                conn.execute(stmt)
                conn.commit()
            except Exception as e:
                conn.rollback()  # Postgres requires rollback after failed ALTER
                msg = str(e).lower()
                # "duplicate column" = SQLite, "already exists" = Postgres,
                # "infailedsqltransaction" = Postgres cascaded error from prior ALTER
                if "duplicate column" not in msg and "already exists" not in msg and "infailedsqltransaction" not in msg:
                    _log.error("Migration FAILED for %s.%s: %s", table, column, e)
                    raise

        for index_name, table, column in _NEW_INDEXES:
            stmt = text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})")
            try:
                conn.execute(stmt)
                conn.commit()
            except Exception as e:
                conn.rollback()
                _log.warning("Index migration warning for %s: %s", index_name, e)

        for index_name, table, column in _NEW_UNIQUE_INDEXES:
            stmt = text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({column})")
            try:
                conn.execute(stmt)
                conn.commit()
            except Exception as e:
                conn.rollback()
                # On prod the legacy global unique index on prospects.email may
                # cause this CREATE to fail until MANUAL-2 runs. Don't crash  -
                # just log so the operator can act on it.
                _log.warning("Unique index migration warning for %s: %s", index_name, e)

    # FIX-13: Backfill NULL user_id rows to the first admin user so that
    # the tightened ownership filters added in this audit don't render
    # legacy single-rep production data invisible.
    _backfill_null_user_ids()


# ── NULL user_id backfill ─────────────────────────────────────────────────────
# When multi-tenant user_id columns were added (FIX-13), existing production rows
# had NULL user_id. This backfill assigns them to the first admin user so they
# remain visible under the new ownership filters.
#
# Each entry: (table_name, timestamp_column_for_safety_check).
# The timestamp column is compared against the first rep's creation date to
# detect rows that might belong to a different user (safety check).
_USER_ID_TABLES: list[tuple[str, str]] = [
    ("prospects",            "created_at"),
    ("campaigns",            "created_at"),
    ("jobs",                 "created_at"),
    ("credit_logs",          "created_at"),
    ("send_logs",            "enrolled_at"),
    ("style_profiles",       "created_at"),
    ("conference_leads",     "created_at"),
    ("conference_inboxes",   "created_at"),
    # P3-1: orchestration + linkedin queue now covered by the safety check
    ("orch_sequence_states", "created_at"),
    ("linkedin_queue",       "created_at"),
]


def _backfill_null_user_ids() -> None:
    """
    Assign legacy NULL user_id rows to the first admin user.

    Multi-rep safety check
    ----------------------
    Before assigning anything, this function inspects every row with
    `user_id IS NULL` and confirms its `created_at` is **strictly older** than
    the first non-admin user account in the DB. If any NULL row was created
    AFTER the first rep was onboarded, the rep may have created it  -  blindly
    assigning it to the admin would silently steal a real rep's data. In that
    case the function refuses to backfill any row in that table and logs a
    CRITICAL warning so the operator can resolve ownership manually.

    This makes the function safe on multi-rep databases that share legacy
    history. On single-rep databases (no rep accounts at all) it falls back
    to the simple "assign all NULL → first admin" behaviour.

    Idempotent: re-running is a no-op once all rows are owned. If no admin
    user exists yet, the function is a no-op so first-run setup keeps working.

    Set `OUTREACH_BACKFILL_ALLOW_RECENT=1` in the environment to bypass the
    safety check (only for one-time manual recovery).
    """
    import logging
    import os
    _log = logging.getLogger(__name__)

    bypass_safety = os.getenv("OUTREACH_BACKFILL_ALLOW_RECENT", "").strip() == "1"

    with engine.connect() as conn:
        try:
            row = conn.execute(text(
                "SELECT id FROM users WHERE role = 'admin' "
                "ORDER BY created_at ASC LIMIT 1"
            )).fetchone()
        except Exception as e:
            # users table missing on the very first run  -  nothing to backfill.
            _log.debug("backfill: users table not ready (%s)", e)
            return

        if not row:
            _log.info("backfill: no admin user yet  -  skipping NULL user_id backfill")
            return

        admin_id = row[0]

        # Find the earliest REAL rep account creation time. We exclude:
        #  - admins (they're not the cutoff)
        #  - common test account email patterns (test@, *test*@)  -  those were
        #    seeded during local development before the real reps existed,
        #    so they create false-positive cutoffs that lock the safety check.
        first_rep_created_at = None
        try:
            rep_row = conn.execute(text(
                "SELECT MIN(created_at) FROM users "
                "WHERE role <> 'admin' "
                "  AND email NOT LIKE '%test%' "
                "  AND email NOT LIKE '%example.com%'"
            )).fetchone()
            if rep_row and rep_row[0] is not None:
                first_rep_created_at = rep_row[0]
        except Exception as e:
            _log.warning("backfill: could not determine first rep creation time (%s)", e)
            try:
                conn.rollback()
            except Exception:
                pass

        backfilled_total = 0
        skipped_tables: list[str] = []

        for table, ts_col in _USER_ID_TABLES:
            # Short-circuit: if there are no NULL rows in this table at all,
            # there is nothing to do  -  skip the safety check entirely. This
            # both avoids noise on tables that are already clean AND avoids
            # tripping postgres' transaction-aborted cascade if a later check
            # fails.
            try:
                null_count = conn.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL")
                ).scalar() or 0
            except Exception as e:
                conn.rollback()
                _log.warning("backfill: could not count NULLs in %s (%s)", table, e)
                continue

            if null_count == 0:
                continue  # Nothing to do  -  clean table.

            # Step 1: safety check  -  refuse to backfill if any NULL row in this
            # table was created on/after the first rep onboarding date.
            if first_rep_created_at is not None and not bypass_safety:
                try:
                    safety = conn.execute(
                        text(
                            f"SELECT COUNT(*) FROM {table} "
                            f"WHERE user_id IS NULL AND {ts_col} >= :cutoff"
                        ),
                        {"cutoff": first_rep_created_at},
                    ).scalar() or 0
                except Exception as e:
                    # Wrong column name or other query failure  -  rollback to
                    # clear the aborted transaction state, then refuse to
                    # backfill this table to be safe.
                    conn.rollback()
                    _log.warning(
                        "backfill: %s.%s safety check failed (%s); "
                        "refusing to backfill",
                        table, ts_col, e,
                    )
                    skipped_tables.append(table)
                    continue

                if safety > 0:
                    _log.critical(
                        "backfill: REFUSING to assign %d NULL %s rows to admin  -  "
                        "%d of them were created on/after the first rep account "
                        "(%s). Resolve ownership manually before restart, or "
                        "set OUTREACH_BACKFILL_ALLOW_RECENT=1 to override.",
                        null_count, table, safety, first_rep_created_at,
                    )
                    skipped_tables.append(table)
                    continue

            # Step 2: safe to backfill  -  all NULL rows predate the first rep.
            try:
                result = conn.execute(
                    text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"),
                    {"uid": admin_id},
                )
                conn.commit()
                count = result.rowcount or 0
                if count:
                    _log.warning(
                        "backfill: assigned %d NULL %s rows to admin %s",
                        count, table, admin_id,
                    )
                    backfilled_total += count
            except Exception as e:
                conn.rollback()
                _log.warning("backfill: skipped %s (%s)", table, e)

        if backfilled_total:
            _log.warning("backfill: total %d rows reassigned to admin", backfilled_total)
        if skipped_tables:
            _log.critical(
                "backfill: SKIPPED %d table(s) for safety: %s. "
                "These rows remain NULL and will be invisible to ownership "
                "filters until manually reassigned.",
                len(skipped_tables), ", ".join(skipped_tables),
            )
