"""Senate eFD search-endpoint ingestion (Senate Slice 2).

Drives the eFD internal JSON endpoint (``POST /search/report/data/``), which returns
DataTables-style rows, paginates through all results for a date window, parses each row
into a ``SenateFiling`` (report UUID + electronic/paper kind), and upserts the index.
"Since last run" uses the persisted watermark (newest ``filed_date``) plus a small overlap;
backfill runs once from ``SENATE_BACKFILL_START_DATE``. Dedup is by report UUID.

The exact DataTables payload field names are best-effort and must be confirmed against the
live eFD form (issue #2, Slice 2). The pagination + parsing logic is the tested core.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models.senate import SenateFiling

WORKER = "senate"
BACKFILL_MARKER = "efd"
DEFAULT_WINDOW_DAYS = 30

logger = logging.getLogger("quant_politicians.senate.search")

_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']')
_VIEW_RE = re.compile(r"/search/view/(ptr|paper)/([^/]+)/?")


# ---------------------------------------------------------------------------
# Parsing (pure)
# ---------------------------------------------------------------------------

def parse_filed_date(value) -> "datetime.date | None":
    if not value:
        return None
    text_value = str(value).strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text_value, fmt).date()
        except ValueError:
            continue
    return None


def parse_search_row(row, base_url: str) -> SenateFiling | None:
    """Parse one DataTables row. Rows without a resolvable report link are skipped."""
    if not isinstance(row, list) or len(row) < 5:
        return None
    href = None
    label = ""
    for cell in row:
        if isinstance(cell, str) and "href" in cell:
            match = _HREF_RE.search(cell)
            if match:
                href = match.group(1)
                label = re.sub(r"<[^>]+>", "", cell).strip()
            break
    if not href:
        return None
    view = _VIEW_RE.search(href)
    if not view:
        return None
    kind, uuid = view.group(1), view.group(2)
    return SenateFiling(
        report_uuid=uuid,
        first=str(row[0]).strip(),
        last=str(row[1]).strip(),
        filer_type=(str(row[2]).strip() or None),
        report_type=label or kind,
        filed_date=parse_filed_date(row[-1]),
        report_url=base_url.rstrip("/") + href,
        is_paper=(kind == "paper"),
    )


def build_search_payload(*, csrf, report_types, filer_types, start_date, end_date, start, length, draw):
    """Best-effort DataTables payload for /search/report/data/."""
    payload = {
        "draw": str(draw),
        "start": str(start),
        "length": str(length),
        "report_types": "[" + ",".join(report_types) + "]",
        "filer_types": "[" + ",".join(filer_types) + "]",
        "submitted_start_date": start_date or "",
        "submitted_end_date": end_date or "",
        "candidate_state": "",
        "senator_state": "",
        "office_id": "",
        "first_name": "",
        "last_name": "",
        "search[value]": "",
        "search[regex]": "false",
    }
    if csrf:
        payload["csrfmiddlewaretoken"] = csrf
    return payload


# ---------------------------------------------------------------------------
# Search (paginated) - session injected
# ---------------------------------------------------------------------------

def search_reports(session, *, report_types, filer_types, start_date, end_date, page_size) -> list[SenateFiling]:
    filings: list[SenateFiling] = []
    start = 0
    draw = 1
    while True:
        payload = build_search_payload(
            csrf=session.csrf_token, report_types=report_types, filer_types=filer_types,
            start_date=start_date, end_date=end_date, start=start, length=page_size, draw=draw,
        )
        resp = session.request(
            "POST", "/search/report/data/", data=payload,
            headers={"Referer": f"{session.base_url}/search/"},
        )
        resp.raise_for_status()
        data = resp.json() or {}
        rows = data.get("data", []) or []
        for row in rows:
            filing = parse_search_row(row, session.base_url)
            if filing:
                filings.append(filing)
        total = int(data.get("recordsTotal", 0) or 0)
        start += page_size
        draw += 1
        if not rows or start >= total:
            break
    return filings


# ---------------------------------------------------------------------------
# Date window + orchestration
# ---------------------------------------------------------------------------

def _iso_to_mdy(iso_date: str) -> str:
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return iso_date


def resolve_date_window(now, watermark, backfill_start, backfill_done, default_days=DEFAULT_WINDOW_DAYS):
    """Return (start_date, end_date, is_backfill) in MM/DD/YYYY."""
    end_date = now.strftime("%m/%d/%Y")
    if backfill_start and not backfill_done:
        return backfill_start, end_date, True
    if watermark:
        return _iso_to_mdy(watermark), end_date, False
    start_date = (now - timedelta(days=default_days)).strftime("%m/%d/%Y")
    return start_date, end_date, False


@dataclass
class SearchSummary:
    seen: int = 0
    new: int = 0
    is_backfill: bool = False
    start_date: str = ""


def run_search_ingest_cycle(*, session, filings_repo, state_repo, now=None, searcher=None) -> SearchSummary:
    now = now or datetime.now(timezone.utc)
    searcher = searcher or search_reports
    backfill_done = BACKFILL_MARKER in state_repo.get_backfill_done(WORKER)
    start_date, end_date, is_backfill = resolve_date_window(
        now, state_repo.get_watermark(WORKER), settings.senate_backfill_start_date, backfill_done,
    )
    logger.info("Senate search window %s..%s (backfill=%s)", start_date, end_date, is_backfill)

    filings = searcher(
        session,
        report_types=settings.parsed_senate_report_types(),
        filer_types=settings.parsed_senate_filer_types(),
        start_date=start_date, end_date=end_date,
        page_size=settings.senate_search_page_size,
    )
    seen = filings_repo.get_seen_report_uuids()
    new_count = sum(1 for f in filings if f.report_uuid not in seen)
    filings_repo.upsert_many(filings)

    max_date = max((f.filed_date for f in filings if f.filed_date), default=None)
    if max_date:
        state_repo.set_watermark(WORKER, max_date.isoformat())
    if is_backfill:
        state_repo.mark_backfill_done(WORKER, BACKFILL_MARKER)

    state_repo.incr_counter(WORKER, "reports_seen", len(filings))
    state_repo.incr_counter(WORKER, "new_reports", new_count)
    return SearchSummary(seen=len(filings), new=new_count, is_backfill=is_backfill, start_date=start_date)
