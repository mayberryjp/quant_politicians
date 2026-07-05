"""0002 - house_filings table.

Stores every ``<Member>`` row from the House annual FD index (the full manifest).
Later slices update mutable columns (``status``, ``doc_url``, ``doc_sha256``,
``page_count``, ``fetch_attempts``, ``last_error``) as filings move through
retrieval -> extraction -> publishing.

Revision ID: 0002_house_filings
Revises: 0001_disclosures_schema
Create Date: 2026-07-05
"""

from alembic import op

revision = "0002_house_filings"
down_revision = "0001_disclosures_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE disclosures.house_filings (
            doc_id          TEXT PRIMARY KEY,
            prefix          TEXT,
            last_name       TEXT NOT NULL DEFAULT '',
            first_name      TEXT NOT NULL DEFAULT '',
            suffix          TEXT,
            filing_type     TEXT NOT NULL DEFAULT '',
            state_dst       TEXT,
            year            INTEGER NOT NULL,
            filing_date     DATE,
            status          TEXT NOT NULL DEFAULT 'new',
            doc_url         TEXT,
            doc_sha256      TEXT,
            page_count      INTEGER,
            fetch_attempts  INTEGER NOT NULL DEFAULT 0,
            last_error      TEXT,
            first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            schema_version  INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    op.execute("CREATE INDEX idx_house_filings_year ON disclosures.house_filings (year)")
    op.execute("CREATE INDEX idx_house_filings_status ON disclosures.house_filings (status)")
    op.execute("CREATE INDEX idx_house_filings_filing_type ON disclosures.house_filings (filing_type)")
    op.execute("CREATE INDEX idx_house_filings_filing_date ON disclosures.house_filings (filing_date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS disclosures.house_filings")
