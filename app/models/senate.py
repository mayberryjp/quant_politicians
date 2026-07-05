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
