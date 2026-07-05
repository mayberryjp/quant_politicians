"""Senate content capture + vision/text LLM extraction (Senate Slice 4).

Electronic reports (HTML) -> extract the table text and send it to the shared LLM as text;
paper reports (PDF) -> render pages to images and send to the vision model. Both converge on
the shared ``parse_trades`` / ``ExtractedTrade`` validation. For electronic reports, the HTML
table's Ticker column is used as a **deterministic cross-check**: an LLM ticker not present in
the HTML is flagged ``needs_review`` (bounding hallucination).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from app.config import settings
from app.models.house import ExtractedTrade, normalize_ticker
from app.services.house_extract import EXTRACTION_PROMPT, ExtractionError, extract_trades_from_images, parse_trades
from app.services.pdf_render import render_pdf_to_images

WORKER = "senate"
logger = logging.getLogger("quant_politicians.senate.extract")


def load_cached_report(cache_dir, sha256: str, ext: str) -> bytes:
    if not sha256:
        raise ValueError("missing content_sha256")
    path = Path(cache_dir) / "senate" / sha256[:2] / f"{sha256}.{ext}"
    if not path.exists():
        raise FileNotFoundError(f"cached report not found: {path}")
    return path.read_bytes()


def extract_html_text(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def parse_html_tickers(html: str) -> list[str]:
    """Best-effort: pull the Ticker column from the report's transactions table."""
    soup = BeautifulSoup(html, "html.parser")
    tickers: list[str] = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not any("ticker" in h for h in headers):
            continue
        col = next(i for i, h in enumerate(headers) if "ticker" in h)
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) > col:
                symbol = normalize_ticker(cells[col].get_text(strip=True))
                if symbol:
                    tickers.append(symbol)
    return tickers


def extract_trades_from_text(text: str, *, llm, max_attempts: int) -> list[ExtractedTrade]:
    if not text.strip():
        return []
    prompt = f"{EXTRACTION_PROMPT}\n\nReport text:\n{text}"
    last_err = None
    for attempt in range(max(1, max_attempts)):
        try:
            return parse_trades(llm.generate_json(prompt, []))
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("senate text extraction attempt %d failed: %s", attempt + 1, exc)
    raise ExtractionError(f"extraction failed after {max_attempts} attempts: {last_err}")


@dataclass
class ExtractSummary:
    considered: int = 0
    extracted_docs: int = 0
    trades: int = 0
    purchases: int = 0
    pages_rendered: int = 0
    mismatches: int = 0
    failed: int = 0


def extract_reports(
    *, filings_repo, extractions_repo, state_repo, cache_dir, llm,
    renderer=None, limit=None, max_attempts=None,
) -> ExtractSummary:
    renderer = renderer or render_pdf_to_images
    limit = limit or settings.extract_batch_size
    max_attempts = max_attempts or settings.llm_max_attempts
    cache_dir = Path(cache_dir)

    rows = filings_repo.get_reports_for_extraction(limit)
    summary = ExtractSummary()

    for report_uuid, sha, is_paper in rows:
        summary.considered += 1
        try:
            if is_paper:
                images = renderer(load_cached_report(cache_dir, sha, "pdf"), settings.max_doc_pages)
                summary.pages_rendered += len(images)
                trades = extract_trades_from_images(images, llm=llm, max_attempts=max_attempts)
                html_tickers: set[str] = set()
            else:
                html = load_cached_report(cache_dir, sha, "html").decode("utf-8", errors="replace")
                trades = extract_trades_from_text(extract_html_text(html), llm=llm, max_attempts=max_attempts)
                html_tickers = set(parse_html_tickers(html))

            enriched = []
            for trade in trades:
                html_ticker = None
                needs_review = trade.ticker is None
                if not is_paper and html_tickers and trade.ticker:
                    if trade.ticker in html_tickers:
                        html_ticker = trade.ticker
                    else:
                        needs_review = True
                        summary.mismatches += 1
                enriched.append((trade, html_ticker, needs_review))

            extractions_repo.insert_extractions(report_uuid, enriched, getattr(llm, "model", None))
            filings_repo.mark_extracted(report_uuid)
            summary.extracted_docs += 1
            summary.trades += len(trades)
            summary.purchases += sum(1 for t in trades if t.transaction_type == "purchase")
            state_repo.incr_counter(WORKER, "extraction_ok")
            logger.info("extracted senate report %s: %d trades", report_uuid, len(trades))
        except Exception as exc:  # noqa: BLE001 - isolated per report
            filings_repo.mark_failed(report_uuid, str(exc)[:500])
            summary.failed += 1
            state_repo.incr_counter(WORKER, "extraction_failed")
            logger.exception("senate extraction failed for %s", report_uuid)

    if summary.pages_rendered:
        state_repo.incr_counter(WORKER, "pages_rendered", summary.pages_rendered)
    if summary.purchases:
        state_repo.incr_counter(WORKER, "purchases_extracted", summary.purchases)
    if summary.mismatches:
        state_repo.incr_counter(WORKER, "ticker_mismatches", summary.mismatches)
    return summary
