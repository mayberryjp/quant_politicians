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
