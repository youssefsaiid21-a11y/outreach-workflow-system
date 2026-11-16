"""add cancelled value to CampaignStatus enum

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-09

Why this exists
---------------
The existing pause_campaign endpoint only flips Instantly's run state  -  it
does NOT remove leads from the campaign. After the Nasser v1.0 incident
(commit e25546a), the operator could pause a campaign and still see
follow-up emails go out because Instantly's SMTP queue had already accepted
them. The cancel-pending kill switch in api/inboxes.py was added as a manual
escape hatch but it was inbox-scoped and easy to forget.

This migration introduces a campaign-level distinction:

- ``paused``     -  reversible. Instantly run state flipped to paused.
                  Leads stay enrolled. Resume restores the campaign.
- ``cancelled``  -  destructive. Leads are purged from Instantly via
                  delete_lead. Local SendLog rows go to ``cancelled`` and
                  EmailSequence rows go to ``unsubscribed``. Resume is
                  blocked (HTTP 409).

The new value lives on CampaignStatus so existing pause/resume logic and
analytics queries don't need a separate column.

Production safety
-----------------
On Postgres, an enum value must be added with ``ALTER TYPE ... ADD VALUE``,
which cannot run inside a transaction. We commit the in-flight transaction
before issuing the ALTER. SQLite (local dev) stores enums as strings so the
value is added implicitly the first time it's written  -  the migration is a
no-op there.

Downgrade is intentionally a no-op: Postgres does not support dropping a
single value from an enum, and we don't want to silently rewrite any
existing ``cancelled`` rows on rollback.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
        op.execute("COMMIT")
        op.execute("ALTER TYPE campaignstatus ADD VALUE IF NOT EXISTS 'cancelled'")
    # SQLite: enum values are stored as plain strings, nothing to do.


def downgrade() -> None:
    """Postgres can't drop a single enum value; intentional no-op."""
    pass
