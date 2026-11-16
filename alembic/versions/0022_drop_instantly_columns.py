"""drop Instantly-specific columns from campaigns and prospects

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-02

Instantly has been removed from the system. All outreach now goes through
LinkedIn (PhantomBuster) or Outlook directly. These columns are no longer
written or read by any application code.
"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.drop_column("instantly_campaign_id")
        batch_op.drop_column("instantly_step_days")

    with op.batch_alter_table("email_sequences") as batch_op:
        batch_op.drop_column("instantly_lead_id")


def downgrade() -> None:
    with op.batch_alter_table("email_sequences") as batch_op:
        batch_op.add_column(sa.Column("instantly_lead_id", sa.String(), nullable=True))

    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.add_column(sa.Column("instantly_step_days", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("instantly_campaign_id", sa.String(), nullable=True))
