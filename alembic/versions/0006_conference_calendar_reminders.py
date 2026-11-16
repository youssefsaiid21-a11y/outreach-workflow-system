"""add calendar_event_ids to conference_leads (Events reminders 2026-04-09)

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-09

Why this exists
---------------
Follow-ups on the Events tab were silently getting missed: the rep would
draft email 1 into their Outlook Drafts folder, send it, then forget to
come back three / seven / fourteen days later to push email 2 and 3.
Nothing in our system nudged them, so large chunks of the sequence just
never went out.

The new behaviour: when email 1 is drafted for a conference lead, we
create one Outlook calendar event per subsequent step on the rep's own
default calendar (via Graph /me/events). Each event is timed against
draft_created_at + the step's day offset from _DEFAULT_SEQ_STEPS (or
the DB-configured conference_sequence_config), scheduled for 09:00 in
the rep's configured reminder timezone, with a 60-minute popup. Clicking
the reminder opens a deep-link back into the sequence modal at the
right step.

This migration adds the storage layer for that feature. Each
ConferenceLead gets a `calendar_event_ids` JSON column shaped like:

    {"email2": "<graph-id>", "email3": "<graph-id>", ...}

The reply poller (conference_reply_poller._match_and_mark) walks this
dict and calls Graph DELETE /me/events/{id} for every remaining event
the moment a lead is marked replied, so closed-out leads stop pinging
the rep's calendar. The same cancellation fires when individual follow-
ups are manually drafted ahead of their reminder (routes_drafts).

Columns
-------
- calendar_event_ids  -  JSON mapping step-key → Graph event id. NULL
  means "no reminders scheduled" (legacy rows + leads drafted before
  this feature shipped).

Production safety
-----------------
Additive only. Nullable with no default; every existing conference_leads
row reads back as "no reminders" and the draft / poller / UI paths all
handle None gracefully (the new features are keyed off `if lead.calendar_event_ids`).

One operational caveat outside this migration: the Graph scope
``Calendars.ReadWrite`` was added in src/services/outlook.py. Existing
connected users will need to reconnect Outlook once  -  MSAL's silent
token acquisition returns no token for a new scope until the user
re-consents. This is expected and surfaced in the dashboard as a
reconnect prompt the first time the poller needs the new scope.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the calendar_event_ids JSON column."""
    op.add_column(
        "conference_leads",
        sa.Column("calendar_event_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Drop the calendar_event_ids column. Used for local rollback only."""
    op.drop_column("conference_leads", "calendar_event_ids")
