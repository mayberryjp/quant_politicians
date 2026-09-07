"""Postgres repository for extracted trades (Slice 3)."""

from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models.house import ExtractedTrade, PublishableExtraction
from app.repository import row_to_dict

log = logging.getLogger("quant_politicians.house.extractions")

_INSERT = text(
    """
    INSERT INTO politicians.house_extractions (
        doc_id, ticker, asset_name, transaction_type, transaction_date,
        amount_range, owner, confidence, raw_json, llm_model, needs_review
    ) VALUES (
        :doc_id, :ticker, :asset_name, :ttype, :tdate,
        :amount, :owner, :confidence, :raw::jsonb, :model, :needs_review
    )
    ON CONFLICT (doc_id, ticker, transaction_type, transaction_date) DO NOTHING
    """
)


class HouseExtractionsRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def insert_extractions(self, doc_id: str, trades: list[ExtractedTrade], llm_model: str | None) -> None:
        if not trades:
            return
        with self.engine.begin() as conn:
            for trade in trades:
                conn.execute(
                    _INSERT,
                    {
                        "doc_id": doc_id,
                        "ticker": trade.ticker,
                        "asset_name": trade.asset_name,
                        "ttype": trade.transaction_type,
                        "tdate": trade.transaction_date,
                        "amount": trade.amount_range,
                        "owner": trade.owner,
                        "confidence": trade.confidence,
                        "raw": json.dumps(trade.model_dump()),
                        "model": llm_model,
                        "needs_review": trade.ticker is None,
                    },
                )

    def get_publishable(self, publish_types: list[str], limit: int) -> list[PublishableExtraction]:
        """Return unpublished, valid-ticker extractions of the requested types,
        joined with member/filing details needed to build the signal."""
        if not publish_types:
            return []
        sql = text(
            """
            SELECT e.id, e.doc_id, e.ticker, e.transaction_type, e.transaction_date,
                   e.amount_range, e.owner, e.llm_model,
                   f.first_name, f.last_name, f.state_dst, f.filing_date
            FROM politicians.house_extractions e
            JOIN politicians.house_filings f ON f.doc_id = e.doc_id
            WHERE e.published = FALSE AND e.ticker IS NOT NULL
              AND e.transaction_type = ANY(:types)
            ORDER BY e.created_at ASC
            LIMIT :limit
            """
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"types": list(publish_types), "limit": limit}).mappings().all()
        return [
            PublishableExtraction(
                id=r["id"], doc_id=r["doc_id"], ticker=r["ticker"],
                transaction_type=r["transaction_type"], transaction_date=r["transaction_date"],
                amount_range=r["amount_range"], owner=r["owner"], llm_model=r["llm_model"],
                first=r["first_name"] or "", last=r["last_name"] or "", state_dst=r["state_dst"],
                filing_date=str(r["filing_date"]) if r["filing_date"] else None,
            )
            for r in rows
        ]

    def mark_published(self, extraction_id: int, idempotency_key: str, signal_result: str) -> None:
        sql = text(
            """
            UPDATE politicians.house_extractions
            SET published = TRUE, idempotency_key = :idem, signal_result = :result
            WHERE id = :id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(sql, {"id": extraction_id, "idem": idempotency_key, "result": signal_result})

    def list_extractions(self, *, doc_id=None, ticker=None, published=None, page=1, page_size=25):
        clauses, params = [], {}
        if doc_id:
            clauses.append("doc_id = :doc_id")
            params["doc_id"] = doc_id
        if ticker:
            clauses.append("ticker = :ticker")
            params["ticker"] = ticker
        if published is not None:
            clauses.append("published = :pub")
            params["pub"] = published
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.engine.connect() as conn:
            total = conn.execute(
                text(f"SELECT count(*) FROM politicians.house_extractions {where}"), params
            ).scalar_one()
            page_params = {**params, "limit": page_size, "offset": (page - 1) * page_size}
            rows = conn.execute(
                text(
                    "SELECT id, doc_id, ticker, asset_name, transaction_type, transaction_date, "
                    "amount_range, owner, confidence, llm_model, idempotency_key, published, "
                    f"signal_result, needs_review, created_at FROM politicians.house_extractions {where} "
                    "ORDER BY created_at DESC, id LIMIT :limit OFFSET :offset"
                ),
                page_params,
            ).mappings().all()
        return [row_to_dict(r) for r in rows], total
