"""add instantly_step_days to campaigns

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-21

Stores Instantly's actual send day schedule as read from their API after launch.
Dashboard reads this to show the real schedule, not our config's intended schedule.
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("instantly_step_days", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "instantly_step_days")
