"""add newsletter tables

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-30

Adds newsletters, newsletter_recipients, and newsletter_unsubscribes tables
for the newsletter feature (Session 1 schema).
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "newsletters",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("track", sa.Enum("prospect", "client", name="newslettertrack"), nullable=False),
        sa.Column("status", sa.Enum("draft", "scheduled", "sending", "sent", "failed", name="newsletterstatus"), server_default="draft"),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("subject_line", sa.Text(), nullable=True),
        sa.Column("preheader_text", sa.Text(), nullable=True),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column("html_rendered", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_newsletters_track", "newsletters", ["track"])
    op.create_index("ix_newsletters_status", "newsletters", ["status"])
    op.create_index("ix_newsletters_owner_id", "newsletters", ["owner_id"])

    op.create_table(
        "newsletter_recipients",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("newsletter_id", sa.String(), sa.ForeignKey("newsletters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_email", sa.String(), nullable=False),
        sa.Column("contact_name", sa.String(), nullable=True),
        sa.Column("contact_company", sa.String(), nullable=True),
        sa.Column("source", sa.String(), server_default="manual"),
        sa.Column("status", sa.Enum("pending", "sent", "delivered", "opened", "clicked", "bounced", "failed", "unsubscribed", name="newsletterrecipientstatus"), server_default="pending"),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("clicked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_nl_recip_newsletter_id", "newsletter_recipients", ["newsletter_id"])
    op.create_index("ix_nl_recip_contact_email", "newsletter_recipients", ["contact_email"])
    op.create_index("ix_nl_recip_status", "newsletter_recipients", ["status"])
    op.create_index("ix_nl_recip_newsletter_email", "newsletter_recipients", ["newsletter_id", "contact_email"], unique=True)

    op.create_table(
        "newsletter_unsubscribes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("unsubscribed_at", sa.DateTime(), nullable=True),
        sa.Column("newsletter_id", sa.String(), sa.ForeignKey("newsletters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_newsletter_unsubscribes_email", "newsletter_unsubscribes", ["email"])


def downgrade() -> None:
    op.drop_table("newsletter_unsubscribes")
    op.drop_table("newsletter_recipients")
    op.drop_table("newsletters")
