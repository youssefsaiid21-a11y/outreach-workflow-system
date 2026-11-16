"""add 'connected' and 'messaged' to prospect/sequence status enums

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-09

Adds LinkedIn-appropriate statuses and fixes existing campaign data
from stale email-oriented statuses (opened/bounced/replied) to
reflect actual LinkedIn activity.
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # 1. Add new enum values
    if is_pg:
        op.execute("ALTER TYPE prospectstatus ADD VALUE IF NOT EXISTS 'connected'")
        op.execute("ALTER TYPE prospectstatus ADD VALUE IF NOT EXISTS 'messaged'")
        op.execute("ALTER TYPE sequencestatus ADD VALUE IF NOT EXISTS 'connected'")
        op.execute("ALTER TYPE sequencestatus ADD VALUE IF NOT EXISTS 'messaged'")
        # Commit the enum changes so they're available for data updates
        op.execute("COMMIT")
        op.execute("BEGIN")

    # 2. Fix existing data — map email statuses to LinkedIn statuses.
    # Uses linkedin_connected flag on prospects and linkedin_queue to
    # determine the correct LinkedIn status.

    if is_pg:
        # IMPORTANT: check for messages FIRST (across ALL old statuses)
        # before mapping to connected/enrolled, so messaged takes priority.

        # -- email_sequences: any old status with message done → messaged
        op.execute(sa.text("""
            UPDATE email_sequences es
            SET status = 'messaged'
            WHERE es.status IN ('opened', 'replied', 'bounced')
              AND EXISTS (
                SELECT 1 FROM linkedin_queue lq
                WHERE lq.prospect_id = es.prospect_id
                  AND lq.campaign_id = es.campaign_id
                  AND lq.action_type = 'message'
                  AND lq.status = 'done'
              )
        """))
        # -- remaining opened/replied/bounced with linkedin_connected → connected
        op.execute(sa.text("""
            UPDATE email_sequences es
            SET status = 'connected'
            WHERE es.status IN ('opened', 'replied', 'bounced')
              AND EXISTS (
                SELECT 1 FROM prospects p
                WHERE p.id = es.prospect_id AND p.linkedin_connected = TRUE
              )
        """))
        # -- everything else still opened/replied/bounced → enrolled
        op.execute(sa.text("""
            UPDATE email_sequences es
            SET status = 'enrolled'
            WHERE es.status IN ('opened', 'replied', 'bounced')
        """))

        # -- prospects: same priority order (messaged first, then connected, then enrolled)
        op.execute(sa.text("""
            UPDATE prospects p
            SET status = 'messaged'
            WHERE p.status IN ('opened', 'replied', 'bounced')
              AND EXISTS (
                SELECT 1 FROM linkedin_queue lq
                WHERE lq.prospect_id = p.id
                  AND lq.action_type = 'message'
                  AND lq.status = 'done'
              )
        """))
        op.execute(sa.text("""
            UPDATE prospects
            SET status = 'connected'
            WHERE status IN ('opened', 'replied', 'bounced') AND linkedin_connected = TRUE
        """))
        op.execute(sa.text("""
            UPDATE prospects
            SET status = 'enrolled'
            WHERE status IN ('opened', 'replied', 'bounced')
        """))


def downgrade() -> None:
    # Reverse data: connected → opened, messaged → replied
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("UPDATE email_sequences SET status = 'opened' WHERE status = 'connected'")
        op.execute("UPDATE email_sequences SET status = 'replied' WHERE status = 'messaged'")
        op.execute("UPDATE prospects SET status = 'opened' WHERE status = 'connected'")
        op.execute("UPDATE prospects SET status = 'replied' WHERE status = 'messaged'")
    # NOTE: PostgreSQL does not support removing enum values.
