"""Postgres repository for House filing index rows (mirrors quant_signals style)."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models.house import HouseFiling

log = logging.getLogger("quant_politicians.house.repo")

_UPSERT = text(
    """
    INSERT INTO disclosures.house_filings (
        doc_id, prefix, last_name, first_name, suffix,
        filing_type, state_dst, year, filing_date, status
    ) VALUES (
        :doc_id, :prefix, :last_name, :first_name, :suffix,
        :filing_type, :state_dst, :year, :filing_date, 'new'
    )
    ON CONFLICT (doc_id) DO UPDATE SET
        filing_type = EXCLUDED.filing_type,
        state_dst   = EXCLUDED.state_dst,
        filing_date = EXCLUDED.filing_date,
        updated_at  = now()
    """
)


def _params(filing: HouseFiling) -> dict:
    return {
        "doc_id": filing.doc_id,
        "prefix": filing.prefix,
        "last_name": filing.last,
        "first_name": filing.first,
        "suffix": filing.suffix,
        "filing_type": filing.filing_type,
        "state_dst": filing.state_dst,
        "year": filing.year,
        "filing_date": filing.filing_date,
    }


class HouseFilingsRepository:
    """Durable index of every House ``<Member>`` filing, keyed by ``doc_id``."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_seen_doc_ids(self, year: int) -> set[str]:
        sql = text("SELECT doc_id FROM disclosures.house_filings WHERE year = :year")
        with self.engine.connect() as conn:
            return set(conn.execute(sql, {"year": year}).scalars().all())

    def upsert_many(self, filings: list[HouseFiling]) -> None:
        if not filings:
            return
        with self.engine.begin() as conn:
            for filing in filings:
                conn.execute(_UPSERT, _params(filing))
