"""add meeting_booked column to conference_leads

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-16

Tracks whether a meeting has been booked with this conference lead.
Boolean flag, defaults to false. Toggled via the dashboard UI.
"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conference_leads",
        sa.Column("meeting_booked", sa.Boolean, nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("conference_leads", "meeting_booked")
