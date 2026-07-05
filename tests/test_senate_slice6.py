"""Senate Slice 6 tests: observability counters (pages_rendered, purchases_extracted) + /stats."""

from __future__ import annotations

import hashlib

from app.redis.repository import StateRepository
from app.services.senate_extract import extract_reports

TABLE_HTML = (
    "<html><body><table>"
    "<tr><th>Date</th><th>Ticker</th><th>Type</th></tr>"
    "<tr><td>01/15/2024</td><td>AAPL</td><td>Purchase</td></tr>"
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

    def generate_json(self, prompt, images):
        return self.response


class TestPagesRenderedCounter:
    def test_paper_render_counts_pages(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        frepo, erepo = FilingsRepo(), ExtractionsRepo()
        sha = cache_report(tmp_path, b"%PDF-data", "pdf")
        frepo.add_fetched("U1", sha, True)
        llm = FakeLLM({"trades": [{"ticker": "MSFT", "transaction_type": "purchase"}]})

        summary = extract_reports(
            filings_repo=frepo, extractions_repo=erepo, state_repo=state,
            cache_dir=tmp_path, llm=llm, renderer=lambda pdf, pages: [b"p1", b"p2", b"p3"],
        )
        assert summary.pages_rendered == 3
        assert state.get_counters("senate", ["pages_rendered"])["pages_rendered"] == 3

    def test_electronic_renders_no_pages(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        frepo, erepo = FilingsRepo(), ExtractionsRepo()
        sha = cache_report(tmp_path, TABLE_HTML.encode(), "html")
        frepo.add_fetched("U1", sha, False)
        llm = FakeLLM({"trades": [{"ticker": "AAPL", "transaction_type": "purchase"}]})

        summary = extract_reports(
            filings_repo=frepo, extractions_repo=erepo, state_repo=state,
            cache_dir=tmp_path, llm=llm,
        )
        assert summary.pages_rendered == 0
        assert state.get_counters("senate", ["pages_rendered"])["pages_rendered"] == 0


class TestPurchasesExtractedCounter:
    def test_counts_only_purchases(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        frepo, erepo = FilingsRepo(), ExtractionsRepo()
        sha = cache_report(tmp_path, TABLE_HTML.encode(), "html")
        frepo.add_fetched("U1", sha, False)
        llm = FakeLLM({"trades": [
            {"ticker": "AAPL", "transaction_type": "purchase"},
            {"ticker": "TSLA", "transaction_type": "sale"},
            {"ticker": "NVDA", "transaction_type": "purchase"},
        ]})

        summary = extract_reports(
            filings_repo=frepo, extractions_repo=erepo, state_repo=state,
            cache_dir=tmp_path, llm=llm,
        )
        assert summary.purchases == 2
        assert state.get_counters("senate", ["purchases_extracted"])["purchases_extracted"] == 2

    def test_no_purchases_leaves_counter_zero(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        frepo, erepo = FilingsRepo(), ExtractionsRepo()
        sha = cache_report(tmp_path, TABLE_HTML.encode(), "html")
        frepo.add_fetched("U1", sha, False)
        llm = FakeLLM({"trades": [{"ticker": "TSLA", "transaction_type": "sale"}]})

        summary = extract_reports(
            filings_repo=frepo, extractions_repo=erepo, state_repo=state,
            cache_dir=tmp_path, llm=llm,
        )
        assert summary.purchases == 0
        assert state.get_counters("senate", ["purchases_extracted"])["purchases_extracted"] == 0


class TestStatsSurfacesCounters:
    def test_stats_reflects_senate_counters(self, app_client, fake_redis):
        state = StateRepository(fake_redis)
        state.incr_counter("senate", "pages_rendered", 5)
        state.incr_counter("senate", "purchases_extracted", 3)

        resp = app_client.get("/politicians-cache/stats")
        senate = resp.json["senate"]
        assert senate["pages_rendered"] == 5
        assert senate["purchases_extracted"] == 3
