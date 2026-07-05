"""Domain models for House financial-disclosure ingestion."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


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
