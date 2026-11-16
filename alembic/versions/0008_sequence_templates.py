"""create sequence_templates table

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-09

Why this exists
---------------
Operators wanted to save a sequence they liked from one campaign and reuse
it on a new campaign without re-running Claude. Per-user with an optional
``is_shared`` flag (default private; owner can flip to share with the rest
of the workspace).

Storage layout
--------------
``steps_json`` is a JSON column holding a list of step dicts:

    [
      {"type": "email", "step_number": 1, "subject": "...", "body": "..."},
      ...
      {"type": "linkedin_connect", "step_number": null, "body": "..."},
      {"type": "linkedin_message",  "step_number": null, "body": "..."},
    ]

JSON instead of flat columns so the schema can grow (more steps, new step
types, A/B variants) without migrating again. ``step_count`` is denormalised
so listings can show the size without parsing JSON.

Production safety
-----------------
Additive only. No data backfill needed  -  every existing campaign already
has its sequences in ``email_sequences``; templates are a brand-new feature.
Downgrade drops the table.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sequence_templates",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_shared", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_sequence_templates_owner_user_id",
        "sequence_templates",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_sequence_templates_is_shared",
        "sequence_templates",
        ["is_shared"],
    )


def downgrade() -> None:
    op.drop_index("ix_sequence_templates_is_shared", table_name="sequence_templates")
    op.drop_index("ix_sequence_templates_owner_user_id", table_name="sequence_templates")
    op.drop_table("sequence_templates")
