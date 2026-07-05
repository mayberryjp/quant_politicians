"""Postgres repository for Senate report index rows (Senate Slice 2)."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models.senate import SenateFiling

log = logging.getLogger("quant_politicians.senate.repo")

_UPSERT = text(
    """
    INSERT INTO disclosures.senate_filings (
        report_uuid, first_name, last_name, state, filer_type,
        report_type, filed_date, report_url, is_paper, status
    ) VALUES (
        :report_uuid, :first, :last, :state, :filer_type,
        :report_type, :filed_date, :report_url, :is_paper, 'new'
    )
    ON CONFLICT (report_uuid) DO UPDATE SET
        report_type = EXCLUDED.report_type,
        filed_date  = EXCLUDED.filed_date,
        report_url  = EXCLUDED.report_url,
        is_paper    = EXCLUDED.is_paper,
        updated_at  = now()
    """
)


def _params(filing: SenateFiling) -> dict:
    return {
        "report_uuid": filing.report_uuid,
        "first": filing.first,
        "last": filing.last,
        "state": filing.state,
        "filer_type": filing.filer_type,
        "report_type": filing.report_type,
        "filed_date": filing.filed_date,
        "report_url": filing.report_url,
        "is_paper": filing.is_paper,
    }


class SenateFilingsRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_seen_report_uuids(self) -> set[str]:
        sql = text("SELECT report_uuid FROM disclosures.senate_filings")
        with self.engine.connect() as conn:
            return set(conn.execute(sql).scalars().all())

    def upsert_many(self, filings: list[SenateFiling]) -> None:
        if not filings:
            return
        with self.engine.begin() as conn:
            for filing in filings:
                conn.execute(_UPSERT, _params(filing))

    def get_reports_for_retrieval(self, limit: int) -> list[tuple[str, str, bool]]:
        sql = text(
            """
            SELECT report_uuid, report_url, is_paper
            FROM disclosures.senate_filings
            WHERE status = 'new' AND report_url IS NOT NULL
            ORDER BY filed_date DESC NULLS LAST
            LIMIT :limit
            """
        )
        with self.engine.connect() as conn:
            return [(r[0], r[1], r[2]) for r in conn.execute(sql, {"limit": limit}).all()]

    def mark_fetched(self, report_uuid: str, content_sha256: str, page_count) -> None:
        sql = text(
            """
            UPDATE disclosures.senate_filings
            SET status = 'fetched', content_sha256 = :sha, page_count = :pages, updated_at = now()
            WHERE report_uuid = :uuid
            """
        )
        with self.engine.begin() as conn:
            conn.execute(sql, {"uuid": report_uuid, "sha": content_sha256, "pages": page_count})

    def mark_failed(self, report_uuid: str, error: str) -> None:
        sql = text(
            """
            UPDATE disclosures.senate_filings
            SET status = 'failed', last_error = :err,
                fetch_attempts = fetch_attempts + 1, updated_at = now()
            WHERE report_uuid = :uuid
            """
        )
        with self.engine.begin() as conn:
            conn.execute(sql, {"uuid": report_uuid, "err": error})
