"""add user_note column to conference_leads

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-16

Separate free-text note field for reps to add custom info after outreach.
Distinct from the 'notes' column which stores data imported from Excel.
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conference_leads", sa.Column("user_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("conference_leads", "user_note")
