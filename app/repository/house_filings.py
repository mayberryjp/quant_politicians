"""Postgres repository for House filing index rows (mirrors quant_signals style)."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models.house import HouseFiling
from app.repository import row_to_dict

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

    def get_filings_for_retrieval(self, filing_types: list[str], limit: int) -> list[HouseFiling]:
        """Return ``new`` filings whose type is in the allowlist (targets to fetch)."""
        if not filing_types:
            return []
        sql = text(
            """
            SELECT doc_id, year, filing_type
            FROM disclosures.house_filings
            WHERE status = 'new' AND filing_type = ANY(:types)
            ORDER BY filing_date DESC NULLS LAST
            LIMIT :limit
            """
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"types": list(filing_types), "limit": limit}).mappings().all()
        return [
            HouseFiling(doc_id=r["doc_id"], year=r["year"], filing_type=r["filing_type"])
            for r in rows
        ]

    def mark_fetched(self, doc_id: str, doc_url: str, doc_sha256: str, page_count: int) -> None:
        sql = text(
            """
            UPDATE disclosures.house_filings
            SET status = 'fetched', doc_url = :doc_url, doc_sha256 = :sha,
                page_count = :pages, updated_at = now()
            WHERE doc_id = :doc_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(sql, {"doc_id": doc_id, "doc_url": doc_url, "sha": doc_sha256, "pages": page_count})

    def mark_failed(self, doc_id: str, error: str) -> None:
        sql = text(
            """
            UPDATE disclosures.house_filings
            SET status = 'failed', last_error = :err,
                fetch_attempts = fetch_attempts + 1, updated_at = now()
            WHERE doc_id = :doc_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(sql, {"doc_id": doc_id, "err": error})

    def get_filings_for_extraction(self, limit: int) -> list[tuple[str, str | None]]:
        """Return (doc_id, doc_sha256) for filings ready to extract (status='fetched')."""
        sql = text(
            """
            SELECT doc_id, doc_sha256
            FROM disclosures.house_filings
            WHERE status = 'fetched'
            ORDER BY filing_date DESC NULLS LAST
            LIMIT :limit
            """
        )
        with self.engine.connect() as conn:
            return [(r[0], r[1]) for r in conn.execute(sql, {"limit": limit}).all()]

    def mark_extracted(self, doc_id: str) -> None:
        sql = text(
            "UPDATE disclosures.house_filings SET status='extracted', updated_at=now() WHERE doc_id=:doc_id"
        )
        with self.engine.begin() as conn:
            conn.execute(sql, {"doc_id": doc_id})

    def list_filings(self, *, status=None, filing_type=None, year=None, page=1, page_size=25):
        clauses, params = [], {}
        if status:
            clauses.append("status = :status")
            params["status"] = status
        if filing_type:
            clauses.append("filing_type = :ft")
            params["ft"] = filing_type
        if year is not None:
            clauses.append("year = :year")
            params["year"] = year
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.engine.connect() as conn:
            total = conn.execute(
                text(f"SELECT count(*) FROM disclosures.house_filings {where}"), params
            ).scalar_one()
            page_params = {**params, "limit": page_size, "offset": (page - 1) * page_size}
            rows = conn.execute(
                text(
                    "SELECT doc_id, prefix, last_name, first_name, suffix, filing_type, state_dst, "
                    "year, filing_date, status, doc_url, doc_sha256, page_count, fetch_attempts, "
                    f"last_error, first_seen_at, updated_at FROM disclosures.house_filings {where} "
                    "ORDER BY filing_date DESC NULLS LAST, doc_id LIMIT :limit OFFSET :offset"
                ),
                page_params,
            ).mappings().all()
        return [row_to_dict(r) for r in rows], total

    def get_filing(self, doc_id: str):
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM disclosures.house_filings WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            ).mappings().first()
        return row_to_dict(row) if row else None
