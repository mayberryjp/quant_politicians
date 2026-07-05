"""Read API for Senate filings and extractions (Senate Slice 7).

Parity with the House read API: list/detail endpoints with filters + pagination,
returning ``{items, total, page, page_size}``. Backed by the durable Postgres tables.
"""

from __future__ import annotations

from bottle import Bottle, HTTPError, request

from app import dependencies
from app.config import settings

sub = Bottle()


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _page_params() -> tuple[int, int]:
    page = max(1, _int(request.query.get("page"), 1))
    size = min(settings.max_page_size, max(1, _int(request.query.get("page_size"), settings.default_page_size)))
    return page, size


def _bool_or_none(value):
    if value in (None, ""):
        return None
    return str(value).lower() in ("1", "true", "yes")


@sub.get("/senate/filings")
def list_filings():
    page, page_size = _page_params()
    repo = dependencies.get_senate_filings_repo()
    items, total = repo.list_filings(
        status=request.query.get("status") or None,
        report_type=request.query.get("report_type") or None,
        is_paper=_bool_or_none(request.query.get("is_paper")),
        page=page,
        page_size=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@sub.get("/senate/filings/<report_uuid>")
def get_filing(report_uuid):
    repo = dependencies.get_senate_filings_repo()
    row = repo.get_filing(report_uuid)
    if row is None:
        raise HTTPError(404, "filing not found")
    return row


@sub.get("/senate/extractions")
def list_extractions():
    page, page_size = _page_params()
    repo = dependencies.get_senate_extractions_repo()
    ticker = request.query.get("ticker") or None
    items, total = repo.list_extractions(
        report_uuid=request.query.get("report_uuid") or None,
        ticker=ticker.upper() if ticker else None,
        published=_bool_or_none(request.query.get("published")),
        page=page,
        page_size=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}
