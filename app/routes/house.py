"""Read API for House filings and extractions (Slice 6)."""

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


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _bool_or_none(value):
    if value in (None, ""):
        return None
    return str(value).lower() in ("1", "true", "yes")


@sub.get("/house/filings")
def list_filings():
    page, page_size = _page_params()
    repo = dependencies.get_filings_repo()
    items, total = repo.list_filings(
        status=request.query.get("status") or None,
        filing_type=request.query.get("filing_type") or None,
        year=_int_or_none(request.query.get("year")),
        page=page,
        page_size=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@sub.get("/house/filings/<doc_id>")
def get_filing(doc_id):
    repo = dependencies.get_filings_repo()
    row = repo.get_filing(doc_id)
    if row is None:
        raise HTTPError(404, "filing not found")
    return row


@sub.get("/house/extractions")
def list_extractions():
    page, page_size = _page_params()
    repo = dependencies.get_extractions_repo()
    ticker = request.query.get("ticker") or None
    items, total = repo.list_extractions(
        doc_id=request.query.get("doc_id") or None,
        ticker=ticker.upper() if ticker else None,
        published=_bool_or_none(request.query.get("published")),
        page=page,
        page_size=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}
