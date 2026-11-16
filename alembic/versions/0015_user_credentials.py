"""add user_credentials table

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-16

Per-user encrypted credentials for external services (PhantomBuster, Instantly, etc.).
Enables multi-user support where each user connects their own accounts.
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_credentials",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("service", sa.String(), nullable=False),
        sa.Column("key_name", sa.String(), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_user_cred_unique", "user_credentials", ["user_id", "service", "key_name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_user_cred_unique", table_name="user_credentials")
    op.drop_table("user_credentials")
