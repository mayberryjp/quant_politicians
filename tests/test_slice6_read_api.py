"""Slice 6 tests: House read API (filters, pagination, 404) with injected fakes."""

from __future__ import annotations

from app.config import settings


class FakeFilingsRepo:
    def __init__(self, items, total):
        self.items = items
        self.total = total
        self.kw = None
        self.single = None

    def list_filings(self, **kw):
        self.kw = kw
        return self.items, self.total

    def get_filing(self, doc_id):
        return self.single


class FakeExtractionsRepo:
    def __init__(self, items, total):
        self.items = items
        self.total = total
        self.kw = None

    def list_extractions(self, **kw):
        self.kw = kw
        return self.items, self.total


class TestFilings:
    def test_list_with_filters_and_pagination(self, app_client, monkeypatch):
        fake = FakeFilingsRepo([{"doc_id": "100001", "status": "fetched"}], 1)
        monkeypatch.setattr("app.dependencies.get_filings_repo", lambda: fake)

        resp = app_client.get("/house/filings?status=fetched&filing_type=P&year=2026&page=2&page_size=10")
        assert resp.status_int == 200
        assert resp.json["items"][0]["doc_id"] == "100001"
        assert resp.json["total"] == 1
        assert resp.json["page"] == 2 and resp.json["page_size"] == 10
        assert fake.kw["status"] == "fetched"
        assert fake.kw["filing_type"] == "P"
        assert fake.kw["year"] == 2026
        assert fake.kw["page"] == 2 and fake.kw["page_size"] == 10

    def test_get_filing_ok(self, app_client, monkeypatch):
        fake = FakeFilingsRepo([], 0)
        fake.single = {"doc_id": "100001", "status": "extracted"}
        monkeypatch.setattr("app.dependencies.get_filings_repo", lambda: fake)

        resp = app_client.get("/house/filings/100001")
        assert resp.status_int == 200
        assert resp.json["doc_id"] == "100001"

    def test_get_filing_404(self, app_client, monkeypatch):
        fake = FakeFilingsRepo([], 0)
        fake.single = None
        monkeypatch.setattr("app.dependencies.get_filings_repo", lambda: fake)

        resp = app_client.get("/house/filings/missing", expect_errors=True)
        assert resp.status_int == 404

    def test_page_size_clamped(self, app_client, monkeypatch):
        fake = FakeFilingsRepo([], 0)
        monkeypatch.setattr("app.dependencies.get_filings_repo", lambda: fake)

        resp = app_client.get("/house/filings?page_size=99999")
        assert resp.json["page_size"] == settings.max_page_size


class TestExtractions:
    def test_list_uppercases_ticker_and_parses_published(self, app_client, monkeypatch):
        fake = FakeExtractionsRepo([{"id": 1, "ticker": "AAPL"}], 1)
        monkeypatch.setattr("app.dependencies.get_extractions_repo", lambda: fake)

        resp = app_client.get("/house/extractions?ticker=aapl&published=true&doc_id=100001")
        assert resp.status_int == 200
        assert resp.json["items"][0]["ticker"] == "AAPL"
        assert fake.kw["ticker"] == "AAPL"
        assert fake.kw["published"] is True
        assert fake.kw["doc_id"] == "100001"
