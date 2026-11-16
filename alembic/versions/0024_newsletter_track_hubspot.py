"""add 'hubspot' value to newslettertrack enum

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-04

Adds the 'hubspot' option to the NewsletterTrack enum so newsletters
can pull recipients from HubSpot's 'To Nurture' deal stage.
Also adds hubspot_last_synced_at column to newsletters table.
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'hubspot' to the newslettertrack enum
    # PostgreSQL requires ALTER TYPE; SQLite stores enums as plain strings.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE newslettertrack ADD VALUE IF NOT EXISTS 'hubspot'")

    # Add column to track last HubSpot sync time
    op.add_column(
        "newsletters",
        sa.Column("hubspot_last_synced_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("newsletters", "hubspot_last_synced_at")
    # NOTE: PostgreSQL does not support removing enum values.
    # The 'hubspot' value will remain in the enum but be unused.
