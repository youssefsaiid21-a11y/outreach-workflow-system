"""add followup_deals table

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-13

Tracks HubSpot deals for the follow-ups machine. Caches deal state,
last activity, and Trello card link for the GTM Analytics dashboard.
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "followup_deals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("hubspot_deal_id", sa.String(), nullable=False),
        sa.Column("deal_name", sa.String(), nullable=True),
        sa.Column("deal_stage", sa.String(), nullable=True),
        sa.Column("deal_stage_id", sa.String(), nullable=True),
        sa.Column("pipeline_id", sa.String(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("owner_name", sa.String(), nullable=True),
        sa.Column("owner_email", sa.String(), nullable=True),
        sa.Column("hubspot_owner_id", sa.String(), nullable=True),
        sa.Column("company_name", sa.String(), nullable=True),
        sa.Column("contact_name", sa.String(), nullable=True),
        sa.Column("contact_email", sa.String(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("days_since_activity", sa.Integer(), nullable=True),
        sa.Column("is_overdue", sa.Boolean(), server_default="false"),
        sa.Column("trello_card_id", sa.String(), nullable=True),
        sa.Column("trello_card_url", sa.String(), nullable=True),
        sa.Column("trello_card_created_at", sa.DateTime(), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_followup_deals_hs_id", "followup_deals", ["hubspot_deal_id"], unique=True)
    op.create_index("ix_followup_deals_overdue", "followup_deals", ["is_overdue"])
    op.create_index("ix_followup_deals_last_activity", "followup_deals", ["last_activity_at"])


def downgrade() -> None:
    op.drop_table("followup_deals")
