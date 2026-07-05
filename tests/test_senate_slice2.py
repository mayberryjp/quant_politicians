"""Senate Slice 2 tests: row parsing, pagination, date window, ingestion, backfill."""

from __future__ import annotations

from datetime import datetime, timezone

from app.config import settings
from app.redis.repository import StateRepository
from app.services.senate_search import (
    build_search_payload,
    parse_filed_date,
    parse_search_row,
    resolve_date_window,
    run_search_ingest_cycle,
    search_reports,
)

BASE = "https://efd.test"


def ptr_row(uuid, first="Jane", last="Doe", date="01/15/2024"):
    return [first, last, "Senator", f'<a href="/search/view/ptr/{uuid}/">Periodic Transaction Report</a>', date]


def paper_row(uuid, date="02/20/2024"):
    return ["John", "Roe", "Senator", f'<a href="/search/view/paper/{uuid}/">Annual Report</a>', date]


class InMemorySenateRepo:
    def __init__(self):
        self.rows: dict[str, object] = {}

    def get_seen_report_uuids(self):
        return set(self.rows)

    def upsert_many(self, filings):
        for f in filings:
            self.rows[f.report_uuid] = f


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


class FakeSession:
    base_url = BASE
    csrf_token = "csrf"

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def request(self, method, path, **kwargs):
        idx = len(self.calls)
        self.calls.append((method, path, kwargs.get("data")))
        return FakeResp(self.pages[idx])


class TestParse:
    def test_filed_date(self):
        assert parse_filed_date("01/15/2024").isoformat() == "2024-01-15"
        assert parse_filed_date("2024-01-15").isoformat() == "2024-01-15"
        assert parse_filed_date("junk") is None

    def test_ptr_row(self):
        f = parse_search_row(ptr_row("U1"), BASE)
        assert f.report_uuid == "U1"
        assert f.is_paper is False
        assert f.report_url == BASE + "/search/view/ptr/U1/"
        assert f.filed_date.isoformat() == "2024-01-15"

    def test_paper_row(self):
        f = parse_search_row(paper_row("U2"), BASE)
        assert f.report_uuid == "U2"
        assert f.is_paper is True

    def test_malformed_rows(self):
        assert parse_search_row(["a", "b", "c"], BASE) is None  # too few cells
        assert parse_search_row(["a", "b", "c", "no link", "01/01/2024"], BASE) is None  # no href


class TestPayload:
    def test_build(self):
        p = build_search_payload(
            csrf="X", report_types=["11"], filer_types=[],
            start_date="01/01/2024", end_date="02/01/2024", start=0, length=100, draw=1,
        )
        assert p["csrfmiddlewaretoken"] == "X"
        assert p["submitted_start_date"] == "01/01/2024"
        assert "11" in p["report_types"]


class TestSearchPagination:
    def test_paginates_all_pages(self):
        pages = [
            {"recordsTotal": 3, "data": [ptr_row("U1"), ptr_row("U2")]},
            {"recordsTotal": 3, "data": [ptr_row("U3")]},
        ]
        sess = FakeSession(pages)
        out = search_reports(
            sess, report_types=["11"], filer_types=[],
            start_date="01/01/2024", end_date="02/01/2024", page_size=2,
        )
        assert [f.report_uuid for f in out] == ["U1", "U2", "U3"]
        assert len(sess.calls) == 2


class TestDateWindow:
    def test_backfill_first(self):
        now = datetime(2026, 7, 5, tzinfo=timezone.utc)
        start, _end, is_backfill = resolve_date_window(now, None, "01/01/2012", False)
        assert (start, is_backfill) == ("01/01/2012", True)

    def test_watermark(self):
        now = datetime(2026, 7, 5, tzinfo=timezone.utc)
        start, _end, is_backfill = resolve_date_window(now, "2026-06-01", "", False)
        assert start == "06/01/2026" and is_backfill is False

    def test_default_window(self):
        now = datetime(2026, 7, 5, tzinfo=timezone.utc)
        start, _end, is_backfill = resolve_date_window(now, None, "", False, default_days=30)
        assert is_backfill is False and start


class TestIngest:
    def test_idempotent_and_since_last_run(self, fake_redis, monkeypatch):
        monkeypatch.setattr(settings, "senate_report_types", "11")
        state = StateRepository(fake_redis)
        repo = InMemorySenateRepo()
        filings = [parse_search_row(ptr_row("U1"), BASE), parse_search_row(ptr_row("U2"), BASE)]

        def searcher(session, **kwargs):
            return list(filings)

        now = datetime(2026, 7, 5, tzinfo=timezone.utc)
        first = run_search_ingest_cycle(session=None, filings_repo=repo, state_repo=state, now=now, searcher=searcher)
        assert (first.seen, first.new) == (2, 2)
        second = run_search_ingest_cycle(session=None, filings_repo=repo, state_repo=state, now=now, searcher=searcher)
        assert second.new == 0
        assert len(repo.rows) == 2
        assert state.get_watermark("senate") == "2024-01-15"

    def test_backfill_once_then_watermark(self, fake_redis, monkeypatch):
        monkeypatch.setattr(settings, "senate_backfill_start_date", "01/01/2012")
        state = StateRepository(fake_redis)
        repo = InMemorySenateRepo()
        windows = []

        def searcher(session, *, report_types, filer_types, start_date, end_date, page_size):
            windows.append(start_date)
            return [parse_search_row(ptr_row("U1"), BASE)]

        now = datetime(2026, 7, 5, tzinfo=timezone.utc)
        first = run_search_ingest_cycle(session=None, filings_repo=repo, state_repo=state, now=now, searcher=searcher)
        assert first.is_backfill is True
        assert windows[0] == "01/01/2012"

        second = run_search_ingest_cycle(session=None, filings_repo=repo, state_repo=state, now=now, searcher=searcher)
        assert second.is_backfill is False  # backfill completed; now uses the watermark
