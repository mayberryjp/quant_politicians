"""0001 - disclosures schema.

Creates the ``politicians`` Postgres schema that later slices populate with
``house_filings`` / ``house_extractions`` (Slice 1+) and the Senate tables
(issue #2). Slice 0 only establishes the schema so ``alembic upgrade head`` is
meaningful in the supervised startup path.

Revision ID: 0001_disclosures_schema
Create Date: 2026-07-05
"""

from alembic import op

revision = "0001_disclosures_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS politicians")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS politicians CASCADE")
