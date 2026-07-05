"""Domain models for House financial-disclosure ingestion."""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, field_validator

_TICKER_RE = re.compile(r"[A-Z0-9.\-]{1,20}")
_TICKER_BLOCKLIST = {"", "N/A", "NA", "NONE", "--", "UNKNOWN", "N.A.", "TBD"}


def normalize_ticker(value) -> str | None:
    """Uppercase + validate a ticker; return None for missing/invalid symbols."""
    if value is None:
        return None
    symbol = str(value).strip().upper()
    if symbol in _TICKER_BLOCKLIST:
        return None
    return symbol if _TICKER_RE.fullmatch(symbol) else None


class HouseFiling(BaseModel):
    """A single ``<Member>`` row parsed from the House annual FD index XML."""

    doc_id: str
    prefix: str | None = None
    last: str = ""
    first: str = ""
    suffix: str | None = None
    filing_type: str = ""
    state_dst: str | None = None
    year: int
    filing_date: date | None = None
    status: str = "new"
    schema_version: int = 1


class ExtractedTrade(BaseModel):
    """A single securities transaction extracted from a filing by the LLM."""

    asset_name: str = ""
    ticker: str | None = None
    transaction_type: str = ""
    transaction_date: str | None = None
    amount_range: str | None = None
    owner: str | None = None
    confidence: float | None = None

    @field_validator("ticker", mode="before")
    @classmethod
    def _normalize_ticker(cls, value):
        return normalize_ticker(value)

    @field_validator("transaction_type", mode="before")
    @classmethod
    def _normalize_type(cls, value):
        return str(value).strip().lower() if value is not None else ""


class PublishableExtraction(BaseModel):
    """A joined extraction + filing row ready to be published as a signal."""

    id: int
    doc_id: str
    ticker: str
    transaction_type: str = ""
    transaction_date: str | None = None
    amount_range: str | None = None
    owner: str | None = None
    llm_model: str | None = None
    first: str = ""
    last: str = ""
    state_dst: str | None = None
    filing_date: str | None = None
