"""baseline  -  capture current schema as alembic v1 (no-op)

Revision ID: 0001
Revises:
Create Date: 2026-04-08

Why this exists
---------------
Before this revision, the project managed schema changes via the ad-hoc
``_NEW_COLUMNS`` list in ``src/db/database.py``. That worked but had no
history, no rollback, and silently mismatched the SQLAlchemy models in
several places (drift). Phase 1 of the rebuild plan introduces alembic to
own future schema changes.

This revision is intentionally a NO-OP. Its only job is to baseline the
existing production schema so that subsequent migrations can build on top
of it. Run ``alembic stamp head`` against any database that already has
the post-audit-#7 schema to mark it as being at this revision.

Known model/schema drift NOT fixed by this baseline (Phase 2 cleanup):
  - accounts.{total_opened, total_replied, total_prospects, meetings_booked}
    exist in DB but not in model (dead engagement counters)
  - audience_segments.prospect_count exists in DB but not in model
  - campaigns.{positive_replies, meetings_booked} exist in DB but not in model
  - app_settings.user_id exists in DB (added by audit #7 FIX-17) but the
    SQLAlchemy model uses a key-prefix convention (`__user__{uid}__{key}`)
    instead of querying the column. The column is vestigial but harmless.
  - prospects.engagement_score exists in DB but not in model
  - ix_prospects_email_unique legacy global index still exists; the new
    composite ix_prospects_user_email coexists. MANUAL-2 from audit #7
    documents the drop.
  - Various index name mismatches (ix_conf_leads_user_id vs
    ix_conference_leads_user_id)  -  Phase 2 normalises.

These are documented but deliberately not auto-applied here because
dropping columns from a populated production database is destructive and
needs an explicit migration with operator review.
"""
from alembic import op  # noqa: F401   -  kept for future revisions
import sqlalchemy as sa  # noqa: F401

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op baseline. Existing schema is already in place."""
    pass


def downgrade() -> None:
    """No-op. There is nothing below the baseline."""
    pass
