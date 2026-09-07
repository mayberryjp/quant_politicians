"""Postgres repository for extracted Senate trades (Senate Slice 4)."""

from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models.senate import SenatePublishableExtraction
from app.repository import row_to_dict

log = logging.getLogger("quant_politicians.senate.extractions")

_INSERT = text(
    """
    INSERT INTO politicians.senate_extractions (
        report_uuid, ticker, asset_name, transaction_type, transaction_date,
        amount_range, owner, confidence, raw_json, llm_model, html_ticker, needs_review
    ) VALUES (
        :report_uuid, :ticker, :asset_name, :ttype, :tdate,
        :amount, :owner, :confidence, :raw::jsonb, :model, :html_ticker, :needs_review
    )
    ON CONFLICT (report_uuid, ticker, transaction_type, transaction_date) DO NOTHING
    """
)


class SenateExtractionsRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def insert_extractions(self, report_uuid: str, enriched, llm_model: str | None) -> None:
        """enriched: iterable of (ExtractedTrade, html_ticker, needs_review)."""
        enriched = list(enriched)
        if not enriched:
            return
        with self.engine.begin() as conn:
            for trade, html_ticker, needs_review in enriched:
                conn.execute(
                    _INSERT,
                    {
                        "report_uuid": report_uuid,
                        "ticker": trade.ticker,
                        "asset_name": trade.asset_name,
                        "ttype": trade.transaction_type,
                        "tdate": trade.transaction_date,
                        "amount": trade.amount_range,
                        "owner": trade.owner,
                        "confidence": trade.confidence,
                        "raw": json.dumps(trade.model_dump()),
                        "model": llm_model,
                        "html_ticker": html_ticker,
                        "needs_review": needs_review,
                    },
                )

    def get_publishable(self, publish_types: list[str], limit: int) -> list[SenatePublishableExtraction]:
        """Return unpublished, valid-ticker extractions of the requested types,
        joined with the filer/report details needed to build the signal."""
        if not publish_types:
            return []
        sql = text(
            """
            SELECT e.id, e.report_uuid, e.ticker, e.transaction_type, e.transaction_date,
                   e.amount_range, e.owner, e.llm_model,
                   f.first_name, f.last_name, f.state, f.filer_type, f.filed_date, f.is_paper
            FROM politicians.senate_extractions e
            JOIN politicians.senate_filings f ON f.report_uuid = e.report_uuid
            WHERE e.published = FALSE AND e.ticker IS NOT NULL
              AND e.transaction_type = ANY(:types)
            ORDER BY e.created_at ASC
            LIMIT :limit
            """
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"types": list(publish_types), "limit": limit}).mappings().all()
        return [
            SenatePublishableExtraction(
                id=r["id"], report_uuid=r["report_uuid"], ticker=r["ticker"],
                transaction_type=r["transaction_type"], transaction_date=r["transaction_date"],
                amount_range=r["amount_range"], owner=r["owner"], llm_model=r["llm_model"],
                first=r["first_name"] or "", last=r["last_name"] or "", state=r["state"],
                filer_type=r["filer_type"], is_paper=r["is_paper"],
                filed_date=str(r["filed_date"]) if r["filed_date"] else None,
            )
            for r in rows
        ]

    def mark_published(self, extraction_id: int, idempotency_key: str, signal_result: str) -> None:
        sql = text(
            """
            UPDATE politicians.senate_extractions
            SET published = TRUE, idempotency_key = :idem, signal_result = :result
            WHERE id = :id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(sql, {"id": extraction_id, "idem": idempotency_key, "result": signal_result})

    def list_extractions(self, *, report_uuid=None, ticker=None, published=None, page=1, page_size=25):
        clauses, params = [], {}
        if report_uuid:
            clauses.append("report_uuid = :uuid")
            params["uuid"] = report_uuid
        if ticker:
            clauses.append("ticker = :ticker")
            params["ticker"] = ticker
        if published is not None:
            clauses.append("published = :pub")
            params["pub"] = published
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.engine.connect() as conn:
            total = conn.execute(
                text(f"SELECT count(*) FROM politicians.senate_extractions {where}"), params
            ).scalar_one()
            page_params = {**params, "limit": page_size, "offset": (page - 1) * page_size}
            rows = conn.execute(
                text(
                    "SELECT id, report_uuid, ticker, asset_name, transaction_type, transaction_date, "
                    "amount_range, owner, confidence, llm_model, html_ticker, idempotency_key, published, "
                    f"signal_result, needs_review, created_at FROM politicians.senate_extractions {where} "
                    "ORDER BY created_at DESC, id LIMIT :limit OFFSET :offset"
                ),
                page_params,
            ).mappings().all()
        return [row_to_dict(r) for r in rows], total
