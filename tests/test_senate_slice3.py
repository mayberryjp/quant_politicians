"""Senate Slice 3 tests: report retrieval (HTML + PDF), dedup, isolation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.redis.repository import StateRepository
from app.services.senate_documents import report_path, retrieve_reports

NO_SLEEP = lambda _s: None  # noqa: E731


@dataclass
class Row:
    report_uuid: str
    report_url: str
    is_paper: bool
    status: str = "new"
    content_sha256: str | None = None
    page_count: int | None = None
    fetch_attempts: int = 0
    last_error: str | None = None


class InMemoryRepo:
    def __init__(self):
        self.rows: dict[str, Row] = {}

    def add(self, uuid, url, is_paper):
        self.rows[uuid] = Row(uuid, url, is_paper)

    def get_reports_for_retrieval(self, limit):
        return [
            (r.report_uuid, r.report_url, r.is_paper)
            for r in self.rows.values() if r.status == "new"
        ][:limit]

    def mark_fetched(self, uuid, sha, pages):
        r = self.rows[uuid]
        r.status, r.content_sha256, r.page_count = "fetched", sha, pages

    def mark_failed(self, uuid, err):
        r = self.rows[uuid]
        r.status, r.last_error = "failed", err
        r.fetch_attempts += 1


class TestReportPath:
    def test_path(self):
        assert report_path("https://efd.test/search/view/ptr/U1/") == "/search/view/ptr/U1/"


class TestRetrieve:
    def test_electronic_html(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        repo = InMemoryRepo()
        repo.add("U1", "https://efd.test/search/view/ptr/U1/", False)

        summary = retrieve_reports(
            session=None, filings_repo=repo, state_repo=state, cache_dir=tmp_path,
            http_get=lambda p: b"<html>report</html>", page_counter=lambda b: 1, sleep_fn=NO_SLEEP,
        )
        assert summary.fetched == 1
        assert repo.rows["U1"].status == "fetched"
        assert repo.rows["U1"].page_count is None
        assert len(list(Path(tmp_path).rglob("*.html"))) == 1
        assert state.get_counters("senate", ["reports_fetched"])["reports_fetched"] == 1

    def test_paper_pdf(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        repo = InMemoryRepo()
        repo.add("U2", "https://efd.test/search/view/paper/U2/", True)

        summary = retrieve_reports(
            session=None, filings_repo=repo, state_repo=state, cache_dir=tmp_path,
            http_get=lambda p: b"%PDF-1.4", page_counter=lambda b: 3, sleep_fn=NO_SLEEP,
        )
        assert summary.fetched == 1
        assert repo.rows["U2"].page_count == 3
        assert len(list(Path(tmp_path).rglob("*.pdf"))) == 1

    def test_dedup_by_hash(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        repo = InMemoryRepo()
        repo.add("U1", "https://efd.test/search/view/ptr/U1/", False)
        repo.add("U2", "https://efd.test/search/view/ptr/U2/", False)

        retrieve_reports(
            session=None, filings_repo=repo, state_repo=state, cache_dir=tmp_path,
            http_get=lambda p: b"<html>same</html>", page_counter=lambda b: 1, sleep_fn=NO_SLEEP,
        )
        assert len(list(Path(tmp_path).rglob("*.html"))) == 1
        assert repo.rows["U1"].content_sha256 == repo.rows["U2"].content_sha256

    def test_failure_isolated(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        repo = InMemoryRepo()
        repo.add("U1", "https://efd.test/search/view/ptr/U1/", False)
        repo.add("U2", "https://efd.test/search/view/ptr/U2/", False)

        def http_get(path):
            if "U1" in path:
                raise ValueError("network boom")
            return b"<html>ok</html>"

        summary = retrieve_reports(
            session=None, filings_repo=repo, state_repo=state, cache_dir=tmp_path,
            http_get=http_get, page_counter=lambda b: 1, sleep_fn=NO_SLEEP,
        )
        assert (summary.fetched, summary.failed) == (1, 1)
        assert repo.rows["U1"].status == "failed"
        assert repo.rows["U1"].fetch_attempts == 1
        assert repo.rows["U2"].status == "fetched"
