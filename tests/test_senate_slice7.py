"""Senate Slice 7 tests: Senate read API (filters, pagination, 404) with injected fakes."""

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

    def get_filing(self, report_uuid):
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
        fake = FakeFilingsRepo([{"report_uuid": "abc-uuid", "status": "fetched"}], 1)
        monkeypatch.setattr("app.dependencies.get_senate_filings_repo", lambda: fake)

        resp = app_client.get("/senate/filings?status=fetched&report_type=ptr&is_paper=true&page=2&page_size=10")
        assert resp.status_int == 200
        assert resp.json["items"][0]["report_uuid"] == "abc-uuid"
        assert resp.json["total"] == 1
        assert resp.json["page"] == 2 and resp.json["page_size"] == 10
        assert fake.kw["status"] == "fetched"
        assert fake.kw["report_type"] == "ptr"
        assert fake.kw["is_paper"] is True
        assert fake.kw["page"] == 2 and fake.kw["page_size"] == 10

    def test_get_filing_ok(self, app_client, monkeypatch):
        fake = FakeFilingsRepo([], 0)
        fake.single = {"report_uuid": "abc-uuid", "status": "extracted"}
        monkeypatch.setattr("app.dependencies.get_senate_filings_repo", lambda: fake)

        resp = app_client.get("/senate/filings/abc-uuid")
        assert resp.status_int == 200
        assert resp.json["report_uuid"] == "abc-uuid"

    def test_get_filing_404(self, app_client, monkeypatch):
        fake = FakeFilingsRepo([], 0)
        fake.single = None
        monkeypatch.setattr("app.dependencies.get_senate_filings_repo", lambda: fake)

        resp = app_client.get("/senate/filings/missing", expect_errors=True)
        assert resp.status_int == 404

    def test_page_size_clamped(self, app_client, monkeypatch):
        fake = FakeFilingsRepo([], 0)
        monkeypatch.setattr("app.dependencies.get_senate_filings_repo", lambda: fake)

        resp = app_client.get("/senate/filings?page_size=99999")
        assert resp.json["page_size"] == settings.max_page_size


class TestExtractions:
    def test_list_uppercases_ticker_and_parses_published(self, app_client, monkeypatch):
        fake = FakeExtractionsRepo([{"id": 1, "ticker": "AAPL"}], 1)
        monkeypatch.setattr("app.dependencies.get_senate_extractions_repo", lambda: fake)

        resp = app_client.get("/senate/extractions?ticker=aapl&published=true&report_uuid=abc-uuid")
        assert resp.status_int == 200
        assert resp.json["items"][0]["ticker"] == "AAPL"
        assert fake.kw["ticker"] == "AAPL"
        assert fake.kw["published"] is True
        assert fake.kw["report_uuid"] == "abc-uuid"
