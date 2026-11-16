"""create sequence_configs table + campaign FK

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-10

Why this exists
---------------
Before 0009, the project had a single global ``app_settings`` row keyed
``custom_sequence_config`` that stored ONE sequence config as JSON. Every
campaign at launch silently inherited whatever was in that row at that
moment, overwriting any prior config and losing the per-campaign link.
The consequence: operators couldn't save multiple named sequences and
couldn't see (after the fact) which sequence a launched campaign had
used. Nasser v1.1's 41 leads were enrolled in the stock hardcoded
LinkedIn companion because of this indirection.

This migration introduces:
  - A ``sequence_configs`` table holding named, per-user, optionally
    shared SequenceConfig blobs. Mirrors the shape of
    ``sequence_templates`` but stores orchestration step structure
    (types + day_offsets + conditions), not email-body snapshots.
  - A nullable ``sequence_config_id`` FK on ``campaigns`` so each
    campaign locks in the sequence it used at creation time.

Production safety
-----------------
Additive only. No data migration. No drops. Existing campaigns keep
``sequence_config_id=NULL`` which makes the launch flow fall back to
the legacy ``custom_sequence_config`` app_setting path  -  so Nasser
v1.1 and every other in-flight campaign is unaffected. Downgrade
drops the new column and table.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sequence_configs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_shared", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # Full SequenceConfig Pydantic model serialised as JSON. Storing
        # as TEXT-like JSON instead of normalised step rows so the schema
        # can grow (new step types, conditions, variant_id, etc.)
        # without another migration.
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_sequence_configs_owner_user_id",
        "sequence_configs",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_sequence_configs_is_shared",
        "sequence_configs",
        ["is_shared"],
    )
    # Per-owner name uniqueness. Two reps can each have a "Default" sequence;
    # a single rep cannot have two sequences with the same name.
    op.create_index(
        "ix_sequence_configs_owner_name_unique",
        "sequence_configs",
        ["owner_user_id", "name"],
        unique=True,
    )

    # Nullable FK on campaigns  -  existing campaigns stay NULL and keep
    # using the legacy global-app-setting fallback at launch time.
    op.add_column(
        "campaigns",
        sa.Column(
            "sequence_config_id",
            sa.String(),
            sa.ForeignKey("sequence_configs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_campaigns_sequence_config_id",
        "campaigns",
        ["sequence_config_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_campaigns_sequence_config_id", table_name="campaigns")
    op.drop_column("campaigns", "sequence_config_id")
    op.drop_index("ix_sequence_configs_owner_name_unique", table_name="sequence_configs")
    op.drop_index("ix_sequence_configs_is_shared", table_name="sequence_configs")
    op.drop_index("ix_sequence_configs_owner_user_id", table_name="sequence_configs")
    op.drop_table("sequence_configs")
