"""add outreach_type and event_date to conference_leads

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-21

Supports the new Pre-Conference Outreach feature. Existing rows default
to outreach_type='post'. The unique index is widened so the same email
can appear in both pre and post outreach for the same conference.
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conference_leads",
        sa.Column("outreach_type", sa.String(), nullable=False, server_default="post"),
    )
    op.add_column(
        "conference_leads",
        sa.Column("event_date", sa.Date(), nullable=True),
    )
    # Widen unique index to include outreach_type
    op.drop_index("ix_conf_leads_conf_email", table_name="conference_leads")
    op.create_index(
        "ix_conf_leads_conf_email_type",
        "conference_leads",
        ["conference_name", "email", "outreach_type"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_conf_leads_conf_email_type", table_name="conference_leads")
    op.create_index(
        "ix_conf_leads_conf_email",
        "conference_leads",
        ["conference_name", "email"],
        unique=True,
    )
    op.drop_column("conference_leads", "event_date")
    op.drop_column("conference_leads", "outreach_type")
