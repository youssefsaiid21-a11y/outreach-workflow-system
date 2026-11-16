"""add linkedin_accounts table and FK columns on campaigns + linkedin_queue

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-10

Phase 1 of multi-account LinkedIn support: LinkedIn accounts as first-class
entities. Each account represents a person's LinkedIn profile + their
PhantomBuster phantom configuration. Campaigns can reference a scrape account
(who finds leads via Sales Nav) and an outreach account (who sends connects/messages).
"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # 1. Create the linkedin_accounts table
    op.create_table(
        "linkedin_accounts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("linkedin_profile_url", sa.String(), nullable=True),
        sa.Column("pb_api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("sales_nav_agent_id", sa.String(), nullable=True),
        sa.Column("connect_agent_id", sa.String(), nullable=True),
        sa.Column("message_agent_id", sa.String(), nullable=True),
        sa.Column("inbox_scraper_agent_id", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # 2. Add FK columns to campaigns
    op.add_column("campaigns", sa.Column("scrape_account_id", sa.String(), nullable=True))
    op.add_column("campaigns", sa.Column("outreach_account_id", sa.String(), nullable=True))

    if is_pg:
        op.create_foreign_key(
            "fk_campaigns_scrape_account",
            "campaigns", "linkedin_accounts",
            ["scrape_account_id"], ["id"],
            ondelete="SET NULL",
        )
        op.create_foreign_key(
            "fk_campaigns_outreach_account",
            "campaigns", "linkedin_accounts",
            ["outreach_account_id"], ["id"],
            ondelete="SET NULL",
        )

    # 3. Add FK column to linkedin_queue
    op.add_column("linkedin_queue", sa.Column("linkedin_account_id", sa.String(), nullable=True))

    if is_pg:
        op.create_foreign_key(
            "fk_linkedin_queue_account",
            "linkedin_queue", "linkedin_accounts",
            ["linkedin_account_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.drop_constraint("fk_linkedin_queue_account", "linkedin_queue", type_="foreignkey")

    op.drop_column("linkedin_queue", "linkedin_account_id")

    if is_pg:
        op.drop_constraint("fk_campaigns_outreach_account", "campaigns", type_="foreignkey")
        op.drop_constraint("fk_campaigns_scrape_account", "campaigns", type_="foreignkey")

    op.drop_column("campaigns", "outreach_account_id")
    op.drop_column("campaigns", "scrape_account_id")

    op.drop_table("linkedin_accounts")
