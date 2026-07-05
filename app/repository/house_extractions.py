"""Postgres repository for extracted trades (Slice 3)."""

from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models.house import ExtractedTrade

log = logging.getLogger("quant_politicians.house.extractions")

_INSERT = text(
    """
    INSERT INTO disclosures.house_extractions (
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
