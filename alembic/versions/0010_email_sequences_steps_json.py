"""add additive steps_json column on email_sequences

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-11

Why this exists
---------------
The ``email_sequences`` table pins the sequence shape to exactly 6
flat columns (``email1_subject``..``email6_body``). Every loop in the
codebase historically assumed ``range(1, 7)``, and raising the cap
required a coordinated schema + code migration  -  a known scalability
blocker flagged in the 2026-04-11 hardcoded-values audit.

This migration is the first half of the structural fix: it adds a
new nullable ``steps_json`` column (JSON) that will eventually hold
the ordered list of steps for a sequence, replacing the 12 flat
columns. Shape::

    [
      {"n": 1, "type": "email", "subject": "...", "body": "...", "subject_b": "..."},
      {"n": 2, "type": "email", "subject": "...", "body": "..."},
      ...,
      {"type": "linkedin_connect", "note": "..."},
      {"type": "linkedin_message", "text": "..."}
    ]

Production safety
-----------------
STRICTLY ADDITIVE.

  - New column is nullable with no server default → every existing row
    gets NULL. No data rewrite. No row-lock escalation.
  - NO code reads from ``steps_json`` yet. All generation, validation,
    rendering, and Instantly-lead construction still read the 12 flat
    columns as before. This migration reserves the space; a separate
    follow-up PR (tracked as "Tier 3b") will flip reads to
    prefer-steps-json-then-flat-columns, backfill existing rows from
    the flat columns, and eventually drop the flat columns.
  - Downgrade is a clean drop_column  -  no data loss because nothing
    has written to it yet.

Test plan
---------
  1. ``alembic upgrade head`` succeeds on a fresh empty DB (unit test
     harness does this automatically).
  2. ``alembic upgrade head`` succeeds on a prod snapshot (41 Nasser
     v1.1 leads + all conference leads, 2026-04-11 snapshot).
  3. After upgrade, every existing row has ``steps_json IS NULL``.
  4. After upgrade, the dashboard + stats endpoint + monitor view all
     render identically to the baseline (because nothing reads the
     new column).
  5. ``alembic downgrade 0009`` removes the column without error.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable JSON column. Populated by a future Tier 3b migration
    # when the read path is ready to prefer it.
    op.add_column(
        "email_sequences",
        sa.Column(
            "steps_json",
            sa.JSON(),
            nullable=True,
            comment=(
                "Ordered list of sequence steps  -  the future replacement "
                "for the 12 flat email{n}_subject / email{n}_body columns. "
                "Currently NOT read by any code; populated by a later "
                "backfill migration once the read path flips."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("email_sequences", "steps_json")
