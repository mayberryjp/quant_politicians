"""Vision-LLM OCR + structured trade extraction (Slice 3).

Renders each fetched filing's PDF to page images, sends them to a vision-capable
Ollama model with a strict-JSON prompt (the model performs OCR *and* extraction in
one step), validates the result against the ``ExtractedTrade`` schema, and persists
the trades. Malformed model responses trigger bounded retries then a counted
failure. Tickers are normalized/validated by the model layer; entries without a
valid ticker are flagged ``needs_review`` and never published.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.models.house import ExtractedTrade
from app.services.pdf_render import render_pdf_to_images

WORKER = "house"
logger = logging.getLogger("quant_politicians.house.extract")

EXTRACTION_PROMPT = (
    "You are reading a U.S. House Periodic Transaction Report (PTR). "
    "Extract every securities transaction shown. Respond with STRICT JSON only "
    "(no prose) in exactly this shape:\n"
    '{"trades": [{"asset_name": "", "ticker": "", '
    '"transaction_type": "purchase|sale|exchange", "transaction_date": "MM/DD/YYYY", '
    '"amount_range": "", "owner": "", "confidence": 0.0}]}\n'
    "Use the ticker symbol exactly as printed; if no ticker is shown, set ticker to null. "
    'If there are no securities transactions, return {"trades": []}.'
)


class ExtractionError(Exception):
    """Raised when the LLM fails to return usable JSON after all retries."""


def parse_trades(raw) -> list[ExtractedTrade]:
    """Validate the model's JSON. Bad top-level shape raises (retried);
    individual malformed items are skipped."""
    if not isinstance(raw, dict) or not isinstance(raw.get("trades"), list):
        raise ValueError("LLM response missing 'trades' list")
    trades: list[ExtractedTrade] = []
    for item in raw["trades"]:
        try:
            trades.append(ExtractedTrade.model_validate(item))
        except Exception:
            logger.warning("skipping unparseable trade item: %r", item)
    return trades


def extract_trades_from_images(images: list[bytes], *, llm, max_attempts: int) -> list[ExtractedTrade]:
    if not images:
        return []
    last_err: Exception | None = None
    for attempt in range(max(1, max_attempts)):
        try:
            raw = llm.generate_json(EXTRACTION_PROMPT, images)
            return parse_trades(raw)
        except Exception as exc:  # noqa: BLE001 - retried, raised below
            last_err = exc
            logger.warning("LLM extraction attempt %d failed: %s", attempt + 1, exc)
    raise ExtractionError(f"extraction failed after {max_attempts} attempts: {last_err}")


def load_cached_pdf(cache_dir, sha256: str | None) -> bytes:
    if not sha256:
        raise ValueError("missing doc_sha256")
    path = Path(cache_dir) / "house" / sha256[:2] / f"{sha256}.pdf"
    if not path.exists():
        raise FileNotFoundError(f"cached pdf not found: {path}")
    return path.read_bytes()


@dataclass
class ExtractSummary:
    considered: int = 0
    extracted_docs: int = 0
    trades: int = 0
    failed: int = 0


def extract_documents(
    *,
    filings_repo,
    extractions_repo,
    state_repo,
    cache_dir,
    llm,
    renderer=None,
    limit=None,
    max_attempts=None,
) -> ExtractSummary:
    """Extract trades for each ``fetched`` filing and persist them."""
    renderer = renderer or render_pdf_to_images
    limit = limit or settings.extract_batch_size
    max_attempts = max_attempts or settings.llm_max_attempts
    cache_dir = Path(cache_dir)

    rows = filings_repo.get_filings_for_extraction(limit)
    summary = ExtractSummary()

    for doc_id, doc_sha256 in rows:
        summary.considered += 1
        try:
            pdf = load_cached_pdf(cache_dir, doc_sha256)
            images = renderer(pdf, settings.max_doc_pages)
            trades = extract_trades_from_images(images, llm=llm, max_attempts=max_attempts)
            extractions_repo.insert_extractions(doc_id, trades, getattr(llm, "model", None))
            filings_repo.mark_extracted(doc_id)
            summary.extracted_docs += 1
            summary.trades += len(trades)
            state_repo.incr_counter(WORKER, "extraction_ok")
            logger.info("extracted %s: %d trades", doc_id, len(trades))
        except Exception as exc:  # noqa: BLE001 - isolated per document
            filings_repo.mark_failed(doc_id, str(exc)[:500])
            summary.failed += 1
            state_repo.incr_counter(WORKER, "extraction_failed")
            logger.exception("extraction failed for %s", doc_id)

    return summary
