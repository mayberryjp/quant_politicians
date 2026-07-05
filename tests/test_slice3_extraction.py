"""Slice 3 tests: ticker normalization, trade parsing, LLM retry, rendering, extraction."""

from __future__ import annotations

import hashlib
import io

import pypdfium2 as pdfium
import pytest

from app.models.house import ExtractedTrade, normalize_ticker
from app.redis.repository import StateRepository
from app.services.house_extract import (
    ExtractionError,
    extract_documents,
    extract_trades_from_images,
    parse_trades,
)
from app.services.pdf_render import render_pdf_to_images


# ---------------------------------------------------------------------------
# Helpers / doubles
# ---------------------------------------------------------------------------

def make_pdf(pages: int = 1) -> bytes:
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    data = buf.getvalue()
    doc.close()
    return data


def cache_pdf(cache_dir, data: bytes) -> str:
    sha = hashlib.sha256(data).hexdigest()
    path = cache_dir / "house" / sha[:2] / f"{sha}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha


class FilingsRepo:
    def __init__(self):
        self.docs: list[tuple[str, str]] = []
        self.status: dict[str, str] = {}
        self.errors: dict[str, str] = {}

    def add_fetched(self, doc_id: str, sha: str):
        self.docs.append((doc_id, sha))
        self.status[doc_id] = "fetched"

    def get_filings_for_extraction(self, limit):
        return [(d, s) for d, s in self.docs if self.status.get(d) == "fetched"][:limit]

    def mark_extracted(self, doc_id):
        self.status[doc_id] = "extracted"

    def mark_failed(self, doc_id, error):
        self.status[doc_id] = "failed"
        self.errors[doc_id] = error


class ExtractionsRepo:
    def __init__(self):
        self.rows: list[tuple[str, ExtractedTrade, str | None]] = []

    def insert_extractions(self, doc_id, trades, model):
        for t in trades:
            self.rows.append((doc_id, t, model))


class FakeLLM:
    model = "test-vision"

    def __init__(self, response=None, raises=False):
        self.response = response
        self.raises = raises
        self.calls = 0

    def generate_json(self, prompt, images):
        self.calls += 1
        if self.raises:
            raise RuntimeError("llm down")
        return self.response


# ---------------------------------------------------------------------------
# Ticker + model validation
# ---------------------------------------------------------------------------

class TestTicker:
    def test_normalize(self):
        assert normalize_ticker("aapl") == "AAPL"
        assert normalize_ticker("  tsla ") == "TSLA"
        assert normalize_ticker("BRK.B") == "BRK.B"
        assert normalize_ticker("N/A") is None
        assert normalize_ticker("") is None
        assert normalize_ticker(None) is None
        assert normalize_ticker("A" * 21) is None
        assert normalize_ticker("$$$") is None

    def test_extracted_trade_normalizes(self):
        t = ExtractedTrade.model_validate(
            {"asset_name": "Apple", "ticker": "aapl", "transaction_type": "Purchase"}
        )
        assert t.ticker == "AAPL"
        assert t.transaction_type == "purchase"

    def test_extracted_trade_invalid_ticker_is_none(self):
        t = ExtractedTrade.model_validate({"ticker": "N/A", "transaction_type": "sale"})
        assert t.ticker is None


# ---------------------------------------------------------------------------
# parse_trades
# ---------------------------------------------------------------------------

class TestParseTrades:
    def test_valid(self):
        raw = {"trades": [
            {"ticker": "AAPL", "transaction_type": "purchase"},
            {"ticker": "tsla", "transaction_type": "sale"},
        ]}
        trades = parse_trades(raw)
        assert [t.ticker for t in trades] == ["AAPL", "TSLA"]

    def test_bad_shape_raises(self):
        for bad in ({}, [], {"trades": "x"}):
            with pytest.raises(ValueError):
                parse_trades(bad)

    def test_skips_unparseable_item(self):
        raw = {"trades": [
            {"ticker": "AAPL", "transaction_type": "purchase"},
            {"confidence": "not-a-number"},
        ]}
        assert len(parse_trades(raw)) == 1


# ---------------------------------------------------------------------------
# extract_trades_from_images (retry semantics)
# ---------------------------------------------------------------------------

class TestExtractFromImages:
    def test_success(self):
        llm = FakeLLM(response={"trades": [{"ticker": "AAPL", "transaction_type": "purchase"}]})
        trades = extract_trades_from_images([b"img"], llm=llm, max_attempts=3)
        assert len(trades) == 1 and trades[0].ticker == "AAPL"
        assert llm.calls == 1

    def test_retries_then_raises(self):
        llm = FakeLLM(raises=True)
        with pytest.raises(ExtractionError):
            extract_trades_from_images([b"img"], llm=llm, max_attempts=3)
        assert llm.calls == 3

    def test_empty_images_no_llm_call(self):
        llm = FakeLLM(response={"trades": []})
        assert extract_trades_from_images([], llm=llm, max_attempts=3) == []
        assert llm.calls == 0


# ---------------------------------------------------------------------------
# Rendering (real PDF -> PNG)
# ---------------------------------------------------------------------------

class TestRender:
    def test_render_real_pdf(self):
        images = render_pdf_to_images(make_pdf(2), max_pages=5, scale=1.0)
        assert len(images) == 2
        assert images[0][:8] == b"\x89PNG\r\n\x1a\n"

    def test_page_cap(self):
        assert len(render_pdf_to_images(make_pdf(3), max_pages=1, scale=1.0)) == 1


# ---------------------------------------------------------------------------
# extract_documents orchestration
# ---------------------------------------------------------------------------

class TestExtractDocuments:
    def test_happy(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        frepo, erepo = FilingsRepo(), ExtractionsRepo()
        sha = cache_pdf(tmp_path, b"%PDF-data")
        frepo.add_fetched("100001", sha)
        llm = FakeLLM(response={"trades": [{
            "asset_name": "Apple", "ticker": "aapl", "transaction_type": "Purchase",
            "transaction_date": "04/15/2026", "amount_range": "$1,001 - $15,000", "owner": "SP",
        }]})

        summary = extract_documents(
            filings_repo=frepo, extractions_repo=erepo, state_repo=state,
            cache_dir=tmp_path, llm=llm, renderer=lambda p, m: [b"img"],
        )

        assert (summary.extracted_docs, summary.trades, summary.failed) == (1, 1, 0)
        assert frepo.status["100001"] == "extracted"
        assert erepo.rows[0][1].ticker == "AAPL"
        assert state.get_counters("house", ["extraction_ok"])["extraction_ok"] == 1

    def test_failure_marks_failed(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        frepo, erepo = FilingsRepo(), ExtractionsRepo()
        sha = cache_pdf(tmp_path, b"%PDF-data")
        frepo.add_fetched("100001", sha)
        llm = FakeLLM(raises=True)

        summary = extract_documents(
            filings_repo=frepo, extractions_repo=erepo, state_repo=state,
            cache_dir=tmp_path, llm=llm, renderer=lambda p, m: [b"img"], max_attempts=2,
        )

        assert (summary.failed, summary.extracted_docs) == (1, 0)
        assert frepo.status["100001"] == "failed"
        assert state.get_counters("house", ["extraction_failed"])["extraction_failed"] == 1
        assert llm.calls == 2

    def test_null_ticker_flagged_not_lost(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        frepo, erepo = FilingsRepo(), ExtractionsRepo()
        sha = cache_pdf(tmp_path, b"%PDF")
        frepo.add_fetched("100002", sha)
        llm = FakeLLM(response={"trades": [{
            "asset_name": "Mystery Holding", "ticker": "N/A", "transaction_type": "purchase",
        }]})

        extract_documents(
            filings_repo=frepo, extractions_repo=erepo, state_repo=state,
            cache_dir=tmp_path, llm=llm, renderer=lambda p, m: [b"img"],
        )
        assert erepo.rows[0][1].ticker is None  # invalid ticker preserved as None

    def test_missing_cache_file_marks_failed(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        frepo, erepo = FilingsRepo(), ExtractionsRepo()
        frepo.add_fetched("100003", "de" * 32)  # no cached file exists
        llm = FakeLLM(response={"trades": []})

        summary = extract_documents(
            filings_repo=frepo, extractions_repo=erepo, state_repo=state,
            cache_dir=tmp_path, llm=llm, renderer=lambda p, m: [b"img"],
        )
        assert summary.failed == 1
        assert frepo.status["100003"] == "failed"
