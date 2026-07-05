"""Postgres repository for extracted Senate trades (Senate Slice 4)."""

from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger("quant_politicians.senate.extractions")

_INSERT = text(
    """
    INSERT INTO disclosures.senate_extractions (
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
