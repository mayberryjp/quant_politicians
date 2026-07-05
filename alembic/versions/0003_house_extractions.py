"""0003 - house_extractions table.

One row per securities transaction extracted from a filing by the vision LLM.
``ticker`` is NULL when the model could not produce a valid symbol (flagged via
``needs_review``); ``idempotency_key`` / ``published`` / ``signal_result`` are set
by the publisher (Slice 4).

Revision ID: 0003_house_extractions
Revises: 0002_house_filings
Create Date: 2026-07-05
"""

from alembic import op

revision = "0003_house_extractions"
down_revision = "0002_house_filings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE disclosures.house_extractions (
            id                SERIAL PRIMARY KEY,
            doc_id            TEXT NOT NULL REFERENCES disclosures.house_filings(doc_id),
            ticker            TEXT,
            asset_name        TEXT NOT NULL DEFAULT '',
            transaction_type  TEXT NOT NULL DEFAULT '',
            transaction_date  TEXT,
            amount_range      TEXT,
            owner             TEXT,
            confidence        DOUBLE PRECISION,
            raw_json          JSONB NOT NULL DEFAULT '{}',
            llm_model         TEXT,
            idempotency_key   TEXT,
            published         BOOLEAN NOT NULL DEFAULT FALSE,
            signal_result     TEXT,
            needs_review      BOOLEAN NOT NULL DEFAULT FALSE,
            schema_version    INTEGER NOT NULL DEFAULT 1,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (doc_id, ticker, transaction_type, transaction_date)
        )
        """
    )
    op.execute("CREATE INDEX idx_house_extractions_doc_id ON disclosures.house_extractions (doc_id)")
    op.execute("CREATE INDEX idx_house_extractions_ticker ON disclosures.house_extractions (ticker)")
    op.execute("CREATE INDEX idx_house_extractions_published ON disclosures.house_extractions (published)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS disclosures.house_extractions")
