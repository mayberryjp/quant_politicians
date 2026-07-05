"""House filing document (PDF) retrieval (Slice 2).

For each targeted filing still in ``new`` status, build its document URL, download
the PDF (polite UA, retry/backoff, size cap), store it content-addressed by SHA-256
(natural dedup), record ``doc_sha256`` + ``page_count``, and advance the row to
``fetched``. Per-filing failures are isolated: they mark the row ``failed`` with
``last_error`` and never abort the batch.

The ``TARGET_FILING_TYPES`` allowlist (default: PTR only) is applied here - the full
index was stored in Slice 1; only trade-bearing reports are downloaded.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pypdfium2 as pdfium

from app.config import settings
from app.models.house import HouseFiling

WORKER = "house"
PTR_FILING_TYPES = {"P"}

logger = logging.getLogger("quant_politicians.house.documents")


def build_document_url(filing: HouseFiling) -> str:
    """PTR filings live under ptr-pdfs/; other statements under financial-pdfs/."""
    base = settings.house_fd_base_url.rstrip("/")
    if filing.filing_type.upper() in PTR_FILING_TYPES:
        return f"{base}/public_disc/ptr-pdfs/{filing.year}/{filing.doc_id}.pdf"
    return f"{base}/public_disc/financial-pdfs/{filing.year}/{filing.doc_id}.pdf"


def _http_get(url: str) -> bytes:
    """Download bytes with a polite UA and a MAX_DOC_BYTES streaming cap."""
    headers = {"User-Agent": settings.http_user_agent}
    with httpx.stream(
        "GET", url, headers=headers,
        timeout=httpx.Timeout(60.0), follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        buf = bytearray()
        for chunk in resp.iter_bytes():
            buf.extend(chunk)
            if len(buf) > settings.max_doc_bytes:
                raise ValueError("document exceeds MAX_DOC_BYTES")
        return bytes(buf)


def fetch_with_retry(url: str, *, http_get, attempts: int, backoff: float, sleep_fn=time.sleep) -> bytes:
    """Call ``http_get`` up to ``attempts`` times with exponential backoff."""
    last_exc: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return http_get(url)
        except Exception as exc:  # noqa: BLE001 - retried, re-raised below
            last_exc = exc
            if i < attempts - 1:
                sleep_fn(backoff * (2 ** i))
    raise last_exc if last_exc else RuntimeError("fetch failed")


def count_pdf_pages(pdf_bytes: bytes) -> int:
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        return len(doc)
    finally:
        doc.close()


def store_pdf(cache_dir: Path, sha256: str, pdf_bytes: bytes) -> Path:
    """Content-addressed storage: identical bytes are written once (dedup)."""
    dest = Path(cache_dir) / "house" / sha256[:2] / f"{sha256}.pdf"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pdf_bytes)
    return dest


@dataclass
class RetrieveSummary:
    considered: int = 0
    fetched: int = 0
    failed: int = 0


def retrieve_documents(
    *,
    filings_repo,
    state_repo,
    cache_dir,
    http_get=None,
    page_counter=None,
    filing_types=None,
    limit=None,
    sleep_fn=time.sleep,
) -> RetrieveSummary:
    """Download PDFs for targeted ``new`` filings and advance their status."""
    http_get = http_get or _http_get
    page_counter = page_counter or count_pdf_pages
    filing_types = filing_types if filing_types is not None else settings.parsed_target_filing_types()
    limit = limit or settings.fetch_batch_size
    cache_dir = Path(cache_dir)

    rows = filings_repo.get_filings_for_retrieval(filing_types, limit)
    summary = RetrieveSummary()

    for filing in rows:
        summary.considered += 1
        url = build_document_url(filing)
        try:
            pdf = fetch_with_retry(
                url, http_get=http_get,
                attempts=settings.fetch_max_attempts,
                backoff=settings.fetch_backoff_seconds,
                sleep_fn=sleep_fn,
            )
            sha = hashlib.sha256(pdf).hexdigest()
            pages = page_counter(pdf)
            store_pdf(cache_dir, sha, pdf)
            filings_repo.mark_fetched(filing.doc_id, url, sha, pages)
            summary.fetched += 1
            state_repo.incr_counter(WORKER, "docs_fetched")
            logger.info("fetched %s (%d pages)", filing.doc_id, pages)
        except Exception as exc:  # noqa: BLE001 - isolated per filing
            filings_repo.mark_failed(filing.doc_id, str(exc)[:500])
            summary.failed += 1
            state_repo.incr_counter(WORKER, "failed")
            logger.exception("failed to retrieve document %s", filing.doc_id)

    return summary
