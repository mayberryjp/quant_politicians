"""Domain models for Senate eFD ingestion."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class SenateFiling(BaseModel):
    """A report row parsed from the eFD search results (DataTables JSON)."""

    report_uuid: str
    first: str = ""
    last: str = ""
    state: str | None = None
    filer_type: str | None = None
    report_type: str = ""
    filed_date: date | None = None
    report_url: str | None = None
    is_paper: bool = False
    status: str = "new"
    schema_version: int = 1


class SenatePublishableExtraction(BaseModel):
    """A joined Senate extraction + filing row ready to be published as a signal."""

    id: int
    report_uuid: str
    ticker: str
    transaction_type: str = ""
    transaction_date: str | None = None
    amount_range: str | None = None
    owner: str | None = None
    llm_model: str | None = None
    first: str = ""
    last: str = ""
    state: str | None = None
    filer_type: str | None = None
    filed_date: str | None = None
    is_paper: bool = False
