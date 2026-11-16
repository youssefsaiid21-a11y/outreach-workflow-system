"""add reply-tracking columns to conference_leads (Events reply cutoff 2026-04-09)

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-09

Why this exists
---------------
The Events (conference) tab had no reply detection at all: Nehal could
draft email 1, the prospect would reply, and the sequence modal would
happily let her push email 2 and email 3 on top of the reply. The
Instantly-side stop_on_reply hardening (#2.3) does not apply because
the conference flow bypasses Instantly entirely  -  drafts go straight
to Outlook via Microsoft Graph, replies land in the user's own inbox,
and nothing in our system ever sees them.

This migration adds the storage layer for a new background poller
(src/services/conference_reply_poller.py) that iterates every user
with a connected Outlook token, hits Graph /me/messages to fetch
recent inbox activity, matches inbound senders against that user's
active conference leads, and marks matches as replied.

Columns
-------
- replied_at           -  when we detected the reply (our clock, UTC)
- reply_subject        -  the reply's Subject header (nullable, up to text max)
- reply_snippet        -  Graph bodyPreview for the reply, ~255 chars
- reply_from_email     -  sender of the reply; may differ from lead.email
                        if the prospect replied from a personal address

Plus a composite index (user_id, replied_at) so the dashboard's
"show only non-replied leads" filter and the poller's match loop both
stay cheap.

Production safety
-----------------
Additive only. All four columns are nullable with no defaults; every
existing conference_leads row reads back as "not yet replied" and
draft flows keep working. The new index is tiny and created AFTER the
columns so the CREATE INDEX cannot race a new row insert.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add reply-tracking columns + supporting index."""
    op.add_column(
        "conference_leads",
        sa.Column("replied_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "conference_leads",
        sa.Column("reply_subject", sa.Text(), nullable=True),
    )
    op.add_column(
        "conference_leads",
        sa.Column("reply_snippet", sa.Text(), nullable=True),
    )
    op.add_column(
        "conference_leads",
        sa.Column("reply_from_email", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_conf_leads_user_replied",
        "conference_leads",
        ["user_id", "replied_at"],
    )


def downgrade() -> None:
    """Drop the index + columns. Only used for local rollback testing."""
    op.drop_index("ix_conf_leads_user_replied", table_name="conference_leads")
    op.drop_column("conference_leads", "reply_from_email")
    op.drop_column("conference_leads", "reply_snippet")
    op.drop_column("conference_leads", "reply_subject")
    op.drop_column("conference_leads", "replied_at")
