"""Senate Slice 4 tests: HTML text/ticker parsing, text extraction, cross-check."""

from __future__ import annotations

import hashlib

from app.redis.repository import StateRepository
from app.services.senate_extract import (
    extract_html_text,
    extract_reports,
    extract_trades_from_text,
    parse_html_tickers,
)

TABLE_HTML = (
    "<html><body><table>"
    "<tr><th>Date</th><th>Ticker</th><th>Type</th></tr>"
    "<tr><td>01/15/2024</td><td>AAPL</td><td>Purchase</td></tr>"
    "<tr><td>01/16/2024</td><td>TSLA</td><td>Sale</td></tr>"
    "</table></body></html>"
)


def cache_report(cache_dir, data: bytes, ext: str) -> str:
    sha = hashlib.sha256(data).hexdigest()
    path = cache_dir / "senate" / sha[:2] / f"{sha}.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha


class FilingsRepo:
    def __init__(self):
        self.docs = []
        self.status = {}

    def add_fetched(self, uuid, sha, is_paper):
        self.docs.append((uuid, sha, is_paper))
        self.status[uuid] = "fetched"

    def get_reports_for_extraction(self, limit):
        return [(u, s, p) for u, s, p in self.docs if self.status.get(u) == "fetched"][:limit]

    def mark_extracted(self, uuid):
        self.status[uuid] = "extracted"

    def mark_failed(self, uuid, err):
        self.status[uuid] = "failed"


class ExtractionsRepo:
    def __init__(self):
        self.rows = []

    def insert_extractions(self, uuid, enriched, model):
        for trade, html_ticker, needs_review in enriched:
            self.rows.append((uuid, trade, html_ticker, needs_review))


class FakeLLM:
    model = "test-vision"

    def __init__(self, response):
        self.response = response
        self.calls = 0

    def generate_json(self, prompt, images):
        self.calls += 1
        return self.response


class TestHtml:
    def test_extract_text(self):
        text = extract_html_text("<p>Hello <b>World</b></p>")
        assert "Hello" in text and "World" in text and "<b>" not in text

    def test_parse_tickers(self):
        assert parse_html_tickers(TABLE_HTML) == ["AAPL", "TSLA"]


class TestTextExtraction:
    def test_extract_trades_from_text(self):
        llm = FakeLLM({"trades": [{"ticker": "aapl", "transaction_type": "purchase"}]})
        trades = extract_trades_from_text("some report text", llm=llm, max_attempts=3)
        assert len(trades) == 1 and trades[0].ticker == "AAPL"

    def test_empty_text_no_call(self):
        llm = FakeLLM({"trades": []})
        assert extract_trades_from_text("   ", llm=llm, max_attempts=3) == []
        assert llm.calls == 0


class TestExtractReports:
    def test_electronic_with_ticker_crosscheck(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        frepo, erepo = FilingsRepo(), ExtractionsRepo()
        sha = cache_report(tmp_path, TABLE_HTML.encode(), "html")
        frepo.add_fetched("U1", sha, False)
        # AAPL matches the HTML table; XYZ does not -> mismatch, needs_review.
        llm = FakeLLM({"trades": [
            {"ticker": "aapl", "transaction_type": "purchase"},
            {"ticker": "XYZ", "transaction_type": "purchase"},
        ]})

        summary = extract_reports(
            filings_repo=frepo, extractions_repo=erepo, state_repo=state,
            cache_dir=tmp_path, llm=llm,
        )
        assert summary.extracted_docs == 1 and summary.trades == 2
        assert summary.mismatches == 1
        by_ticker = {row[1].ticker: row for row in erepo.rows}
        assert by_ticker["AAPL"][2] == "AAPL"  # html_ticker matched
        assert by_ticker["AAPL"][3] is False  # not flagged
        assert by_ticker["XYZ"][3] is True  # flagged needs_review
        assert state.get_counters("senate", ["ticker_mismatches"])["ticker_mismatches"] == 1

    def test_paper_uses_vision(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        frepo, erepo = FilingsRepo(), ExtractionsRepo()
        sha = cache_report(tmp_path, b"%PDF-data", "pdf")
        frepo.add_fetched("U2", sha, True)
        llm = FakeLLM({"trades": [{"ticker": "MSFT", "transaction_type": "purchase"}]})

        summary = extract_reports(
            filings_repo=frepo, extractions_repo=erepo, state_repo=state,
            cache_dir=tmp_path, llm=llm, renderer=lambda pdf, pages: [b"img"],
        )
        assert summary.extracted_docs == 1 and summary.trades == 1
        assert erepo.rows[0][1].ticker == "MSFT"
        assert frepo.status["U2"] == "extracted"

    def test_missing_cache_marks_failed(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        frepo, erepo = FilingsRepo(), ExtractionsRepo()
        frepo.add_fetched("U3", "de" * 32, False)  # no cached file
        llm = FakeLLM({"trades": []})

        summary = extract_reports(
            filings_repo=frepo, extractions_repo=erepo, state_repo=state,
            cache_dir=tmp_path, llm=llm,
        )
        assert summary.failed == 1
        assert frepo.status["U3"] == "failed"
