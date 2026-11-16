"""Alembic environment configuration for the Outreach System.

Reads `DATABASE_URL` from the project's `.env` (via python-dotenv) so the
same connection string drives the app and the migrations. Imports the
SQLAlchemy `Base.metadata` from `src.db` so `--autogenerate` can detect
schema drift between the model definitions and the live database.
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# ── Make `src.*` importable when alembic runs from `outreach-system/` ──
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent  # outreach-system/
sys.path.insert(0, str(_PROJECT_ROOT))

# ── Load .env so DATABASE_URL is available ──
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

# ── Import the SQLAlchemy Base + register all models ──
# Importing src.db.database would trigger init_db side-effects we don't want
# during a migration run, so we import the Base directly and then explicitly
# import each models module to register its tables on Base.metadata.
from src.db.database import Base  # noqa: E402
from src.db import models  # noqa: F401, E402   -  registers core tables
try:
    from src.orchestration import orm as _orch_orm  # noqa: F401, E402
except ImportError:
    pass  # orchestration ORM is optional

target_metadata = Base.metadata

# ── Alembic config object ──
config = context.config

# Override sqlalchemy.url with the env var (so we can use the same .env
# the application uses without duplicating secrets in alembic.ini).
_db_url = os.getenv("DATABASE_URL", "").strip()
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits SQL to stdout instead of touching a real database. Useful for
    generating a migration script for review before applying.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Match-by-type detection helps `--autogenerate` notice column type
        # drift (e.g. VARCHAR -> TEXT) instead of silently ignoring it.
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
