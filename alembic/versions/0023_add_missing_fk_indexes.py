"""Add missing indexes on foreign key columns

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-03

SWAT-H12: Several ForeignKey columns used in joins and filters lacked indexes,
causing full table scans on cascade deletes and common queries. Adds indexes to:
  - send_logs.campaign_id, send_logs.prospect_id, send_logs.sequence_id
  - replies.sequence_id
  - linkedin_queue.campaign_id
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_send_logs_campaign_id", "send_logs", ["campaign_id"])
    op.create_index("ix_send_logs_prospect_id", "send_logs", ["prospect_id"])
    op.create_index("ix_send_logs_sequence_id", "send_logs", ["sequence_id"])
    op.create_index("ix_replies_sequence_id", "replies", ["sequence_id"])
    op.create_index("ix_linkedin_queue_campaign_id", "linkedin_queue", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_linkedin_queue_campaign_id", "linkedin_queue")
    op.drop_index("ix_replies_sequence_id", "replies")
    op.drop_index("ix_send_logs_sequence_id", "send_logs")
    op.drop_index("ix_send_logs_prospect_id", "send_logs")
    op.drop_index("ix_send_logs_campaign_id", "send_logs")
