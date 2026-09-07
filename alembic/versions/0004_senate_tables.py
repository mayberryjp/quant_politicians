"""0004 - senate_filings + senate_extractions tables.

Durable state for the Senate eFD worker (issue #2). Mirrors the House tables but
keyed by the eFD report UUID; ``is_paper`` distinguishes scanned reports, and
``senate_extractions.html_ticker`` records the HTML table ticker used to
cross-check the LLM output.

Revision ID: 0004_senate_tables
Revises: 0003_house_extractions
Create Date: 2026-07-05
"""

from alembic import op

revision = "0004_senate_tables"
down_revision = "0003_house_extractions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE politicians.senate_filings (
            report_uuid     TEXT PRIMARY KEY,
            first_name      TEXT NOT NULL DEFAULT '',
            last_name       TEXT NOT NULL DEFAULT '',
            state           TEXT,
            filer_type      TEXT,
            report_type     TEXT NOT NULL DEFAULT '',
            filed_date      DATE,
            report_url      TEXT,
            is_paper        BOOLEAN NOT NULL DEFAULT FALSE,
            status          TEXT NOT NULL DEFAULT 'new',
            content_sha256  TEXT,
            page_count      INTEGER,
            fetch_attempts  INTEGER NOT NULL DEFAULT 0,
            last_error      TEXT,
            first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            schema_version  INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    op.execute("CREATE INDEX idx_senate_filings_status ON politicians.senate_filings (status)")
    op.execute("CREATE INDEX idx_senate_filings_report_type ON politicians.senate_filings (report_type)")
    op.execute("CREATE INDEX idx_senate_filings_filed_date ON politicians.senate_filings (filed_date)")

    op.execute(
        """
        CREATE TABLE politicians.senate_extractions (
            id                SERIAL PRIMARY KEY,
            report_uuid       TEXT NOT NULL REFERENCES politicians.senate_filings(report_uuid),
            ticker            TEXT,
            asset_name        TEXT NOT NULL DEFAULT '',
            transaction_type  TEXT NOT NULL DEFAULT '',
            transaction_date  TEXT,
            amount_range      TEXT,
            owner             TEXT,
            confidence        DOUBLE PRECISION,
            raw_json          JSONB NOT NULL DEFAULT '{}',
            llm_model         TEXT,
            html_ticker       TEXT,
            idempotency_key   TEXT,
            published         BOOLEAN NOT NULL DEFAULT FALSE,
            signal_result     TEXT,
            needs_review      BOOLEAN NOT NULL DEFAULT FALSE,
            schema_version    INTEGER NOT NULL DEFAULT 1,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (report_uuid, ticker, transaction_type, transaction_date)
        )
        """
    )
    op.execute("CREATE INDEX idx_senate_extractions_report_uuid ON politicians.senate_extractions (report_uuid)")
    op.execute("CREATE INDEX idx_senate_extractions_ticker ON politicians.senate_extractions (ticker)")
    op.execute("CREATE INDEX idx_senate_extractions_published ON politicians.senate_extractions (published)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS politicians.senate_extractions")
    op.execute("DROP TABLE IF EXISTS politicians.senate_filings")
