"""add version column to orch_sequence_states for optimistic locking

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-15

Why this exists
---------------
Concurrent writers (scheduler, ingestion webhook, reconciler) can race on
the same orch_sequence_states row. SQLAlchemy's built-in ``version_id_col``
adds a ``WHERE version = :old`` predicate to every UPDATE - if two writers
read the same version, only the first commit wins and the second raises
``StaleDataError``. The loser retries on the next tick (scheduler) or is
silently skipped (ingestion/reconciler), which is the correct behaviour
since the row has already been advanced.

Production safety
-----------------
Additive only. ``server_default="0"`` backfills every existing row so
running sequences keep working without interruption.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add optimistic-locking version counter."""
    op.add_column(
        "orch_sequence_states",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    """Drop the version column. Only used for local rollback testing."""
    op.drop_column("orch_sequence_states", "version")
