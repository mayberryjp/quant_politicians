"""Senate report retrieval (Senate Slice 3): electronic HTML + paper PDF.

For each ``new`` report, fetch its URL through the authorized eFD session (retry +
backoff + size cap), store the content addressed by SHA-256 (natural dedup), record
``page_count`` for paper PDFs, and advance the row to ``fetched``. Electronic reports
are stored as HTML; paper reports as PDF. Per-report failures are isolated.

Reuses the shared retry + PDF page-count helpers from the House document layer.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.config import settings
from app.services.house_documents import count_pdf_pages, fetch_with_retry

WORKER = "senate"
logger = logging.getLogger("quant_politicians.senate.documents")


def report_path(report_url: str) -> str:
    """The path (+query) of a report URL, for session.request(base_url + path)."""
    parts = urlsplit(report_url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _session_get_bytes(session, path: str) -> bytes:
    resp = session.request("GET", path)
    resp.raise_for_status()
    data = resp.content
    if len(data) > settings.max_doc_bytes:
        raise ValueError("report exceeds MAX_DOC_BYTES")
    return data


def store_content(cache_dir, sha256: str, data: bytes, ext: str) -> Path:
    dest = Path(cache_dir) / "senate" / sha256[:2] / f"{sha256}.{ext}"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return dest


@dataclass
class RetrieveSummary:
    considered: int = 0
    fetched: int = 0
    failed: int = 0


def retrieve_reports(
    *,
    session,
    filings_repo,
    state_repo,
    cache_dir,
    http_get=None,
    page_counter=None,
    limit=None,
    sleep_fn=time.sleep,
) -> RetrieveSummary:
    page_counter = page_counter or count_pdf_pages
    limit = limit or settings.fetch_batch_size
    cache_dir = Path(cache_dir)
    getter = http_get or (lambda path: _session_get_bytes(session, path))

    rows = filings_repo.get_reports_for_retrieval(limit)
    summary = RetrieveSummary()

    for report_uuid, report_url, is_paper in rows:
        summary.considered += 1
        try:
            data = fetch_with_retry(
                report_path(report_url), http_get=getter,
                attempts=settings.fetch_max_attempts,
                backoff=settings.fetch_backoff_seconds, sleep_fn=sleep_fn,
            )
            sha = hashlib.sha256(data).hexdigest()
            if is_paper:
                page_count = page_counter(data)
                store_content(cache_dir, sha, data, "pdf")
            else:
                page_count = None
                store_content(cache_dir, sha, data, "html")
            filings_repo.mark_fetched(report_uuid, sha, page_count)
            summary.fetched += 1
            state_repo.incr_counter(WORKER, "reports_fetched")
            logger.info("fetched senate report %s (paper=%s)", report_uuid, is_paper)
        except Exception as exc:  # noqa: BLE001 - isolated per report
            filings_repo.mark_failed(report_uuid, str(exc)[:500])
            summary.failed += 1
            state_repo.incr_counter(WORKER, "failed")
            logger.exception("failed to retrieve senate report %s", report_uuid)

    return summary
