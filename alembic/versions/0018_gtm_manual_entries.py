"""add gtm_manual_entries table

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-13

Stores manually-entered GTM metrics (meetings booked, referrals, inbound
leads detected by the poller) for the unified GTM Analytics dashboard.
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gtm_manual_entries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("metric_key", sa.String(), nullable=False),
        sa.Column("month", sa.String(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_gtm_manual_cat_month", "gtm_manual_entries", ["category", "month"])
    op.create_index("ix_gtm_manual_user_month", "gtm_manual_entries", ["user_id", "month"])


def downgrade() -> None:
    op.drop_table("gtm_manual_entries")
