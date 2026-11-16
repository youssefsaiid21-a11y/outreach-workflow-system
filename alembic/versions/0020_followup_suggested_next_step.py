"""add suggested_next_step to followup_deals

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-14

AI-generated one-line next action based on HubSpot deal notes.
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("followup_deals", sa.Column("suggested_next_step", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("followup_deals", "suggested_next_step")
