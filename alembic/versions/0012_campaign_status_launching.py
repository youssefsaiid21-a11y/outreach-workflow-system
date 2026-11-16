"""add launching value to CampaignStatus enum

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-15

Why this exists
---------------
The launch flow has multiple stages (create Instantly campaign, enrol leads,
activate). If the process crashes mid-way, the campaign is stuck in ``draft``
with an Instantly campaign already created but no leads enrolled and no
activation. The ``launching`` status marks this intermediate state so the
dashboard shows a distinct pill and the resume-launch path can detect it.

Production safety
-----------------
Same pattern as 0007 (cancelled): Postgres requires ``ALTER TYPE ... ADD VALUE``
outside a transaction. SQLite stores enums as strings, so no DDL is needed.

Downgrade is intentionally a no-op: Postgres does not support dropping a
single value from an enum.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
        op.execute("COMMIT")
        op.execute("ALTER TYPE campaignstatus ADD VALUE IF NOT EXISTS 'launching'")
    # SQLite: enum values are stored as plain strings, nothing to do.


def downgrade() -> None:
    """Postgres can't drop a single enum value; intentional no-op."""
    pass
