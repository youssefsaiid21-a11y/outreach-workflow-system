"""partial unique index on orch_engagement_events for email_sent dedupe (hardening 1.3)

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-09

Why this exists
---------------
The Nasser v1.0 incident took >12 hours to spot because nothing in the
ingestion pipeline noticed it was recording duplicate ``email_sent``
events for the same lead+step. The receiver complaints surfaced it long
after the duplicate sends had already gone out.

Adding a partial unique index on
``(lead_id, event_type, event_metadata->>'sequence_step')`` for the
``email_sent`` event type means a duplicate insert raises an
``IntegrityError`` which the existing P4-2 savepoint wrapper catches as
a WARNING with the event_id and lead_id. We then SEE the duplicate
attempt in the logs the moment it happens  -  instead of finding out
hours later when a recipient complains.

Production safety
-----------------
Additive only  -  no existing rows are touched. The migration first
de-duplicates any pre-existing duplicates by deleting all but the
earliest row per (lead_id, sequence_step). Without this, the CREATE
INDEX would fail on existing data.

The index uses the ``json/jsonb ->>`` operator on Postgres to extract
the ``sequence_step`` from ``event_metadata``. SQLite has no equivalent
in CREATE INDEX, so the index is Postgres-only and the SQLite path
relies on application-level dedupe (not necessary in tests anyway  -
test sessions don't go through the real ingestion pipeline).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the partial unique index after de-duplicating any existing
    duplicates so the CREATE doesn't fail on legacy rows."""
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else ""

    if dialect != "postgresql":
        # SQLite (test path)  -  no jsonb operator support; skip silently.
        return

    # 1. Find duplicates and keep the earliest occurred_at per
    #    (lead_id, sequence_step). Delete the rest.
    op.execute(
        """
        WITH ranked AS (
            SELECT event_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY lead_id,
                                    event_type,
                                    (event_metadata->>'sequence_step')
                       ORDER BY occurred_at ASC, event_id ASC
                   ) AS rn
            FROM orch_engagement_events
            WHERE event_type = 'email_sent'
              AND event_metadata->>'sequence_step' IS NOT NULL
        )
        DELETE FROM orch_engagement_events
        WHERE event_id IN (SELECT event_id FROM ranked WHERE rn > 1);
        """
    )

    # 2. Create the partial unique index. Postgres-only (uses ->>).
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_orch_events_email_sent_dedupe
        ON orch_engagement_events (
            lead_id,
            event_type,
            (event_metadata->>'sequence_step')
        )
        WHERE event_type = 'email_sent'
          AND event_metadata->>'sequence_step' IS NOT NULL;
        """
    )


def downgrade() -> None:
    """Drop the index. Does NOT restore the de-duped rows  -  they were
    bad data."""
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else ""
    if dialect != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_orch_events_email_sent_dedupe;")
