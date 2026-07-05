"""Slice 2 tests: House filing document retrieval (URL, retry, dedup, isolation)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from app.models.house import HouseFiling
from app.redis.repository import StateRepository
from app.services.house_documents import (
    build_document_url,
    count_pdf_pages,
    fetch_with_retry,
    retrieve_documents,
)


# ---------------------------------------------------------------------------
# Helpers
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


@dataclass
class FakeRow:
    doc_id: str
    year: int
    filing_type: str
    status: str = "new"
    doc_url: str | None = None
    doc_sha256: str | None = None
    page_count: int | None = None
    fetch_attempts: int = 0
    last_error: str | None = None


class InMemoryRepo:
    def __init__(self) -> None:
        self.rows: dict[str, FakeRow] = {}

    def add(self, doc_id: str, year: int, filing_type: str, status: str = "new") -> None:
        self.rows[doc_id] = FakeRow(doc_id, year, filing_type, status)

    def get_filings_for_retrieval(self, filing_types, limit):
        types = {t.upper() for t in filing_types}
        selected = [
            r for r in self.rows.values()
            if r.status == "new" and r.filing_type.upper() in types
        ]
        return [
            HouseFiling(doc_id=r.doc_id, year=r.year, filing_type=r.filing_type)
            for r in selected[:limit]
        ]

    def mark_fetched(self, doc_id, doc_url, doc_sha256, page_count):
        r = self.rows[doc_id]
        r.status, r.doc_url, r.doc_sha256, r.page_count = "fetched", doc_url, doc_sha256, page_count

    def mark_failed(self, doc_id, error):
        r = self.rows[doc_id]
        r.status, r.last_error = "failed", error
        r.fetch_attempts += 1


NO_SLEEP = lambda _s: None  # noqa: E731


# ---------------------------------------------------------------------------
# URL building
# ---------------------------------------------------------------------------

class TestUrl:
    def test_ptr_url(self):
        url = build_document_url(HouseFiling(doc_id="100001", year=2026, filing_type="P"))
        assert url.endswith("/public_disc/ptr-pdfs/2026/100001.pdf")

    def test_other_url(self):
        url = build_document_url(HouseFiling(doc_id="9001", year=2026, filing_type="C"))
        assert url.endswith("/public_disc/financial-pdfs/2026/9001.pdf")


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

class TestRetry:
    def test_succeeds_after_failures(self):
        calls = {"n": 0}

        def http_get(url):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("transient")
            return b"ok"

        out = fetch_with_retry("u", http_get=http_get, attempts=3, backoff=0.01, sleep_fn=NO_SLEEP)
        assert out == b"ok"
        assert calls["n"] == 3

    def test_exhausts_and_raises(self):
        calls = {"n": 0}

        def http_get(url):
            calls["n"] += 1
            raise ValueError("always")

        with pytest.raises(ValueError):
            fetch_with_retry("u", http_get=http_get, attempts=3, backoff=0.01, sleep_fn=NO_SLEEP)
        assert calls["n"] == 3


# ---------------------------------------------------------------------------
# PDF page count (real PDF)
# ---------------------------------------------------------------------------

class TestPageCount:
    def test_count_pages(self):
        assert count_pdf_pages(make_pdf(3)) == 3


# ---------------------------------------------------------------------------
# Retrieval orchestration
# ---------------------------------------------------------------------------

class TestRetrieve:
    def test_happy_path_targets_only(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        repo = InMemoryRepo()
        repo.add("100001", 2026, "P")
        repo.add("100002", 2026, "P")
        repo.add("9001", 2026, "C")  # non-PTR, must be ignored

        summary = retrieve_documents(
            filings_repo=repo, state_repo=state, cache_dir=tmp_path,
            http_get=lambda u: b"%PDF-fake",
            page_counter=lambda b: 2,
            filing_types=["P"], sleep_fn=NO_SLEEP,
        )

        assert (summary.considered, summary.fetched, summary.failed) == (2, 2, 0)
        assert repo.rows["100001"].status == "fetched"
        assert repo.rows["100001"].page_count == 2
        assert repo.rows["100001"].doc_sha256
        assert repo.rows["9001"].status == "new"
        assert state.get_counters("house", ["docs_fetched"])["docs_fetched"] == 2

    def test_dedup_by_content_hash(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        repo = InMemoryRepo()
        repo.add("100001", 2026, "P")
        repo.add("100002", 2026, "P")

        retrieve_documents(
            filings_repo=repo, state_repo=state, cache_dir=tmp_path,
            http_get=lambda u: b"%PDF-identical",
            page_counter=lambda b: 1,
            filing_types=["P"], sleep_fn=NO_SLEEP,
        )

        stored = list(Path(tmp_path).rglob("*.pdf"))
        assert len(stored) == 1  # identical bytes stored once
        assert repo.rows["100001"].doc_sha256 == repo.rows["100002"].doc_sha256

    def test_failure_isolated(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        repo = InMemoryRepo()
        repo.add("100001", 2026, "P")
        repo.add("100002", 2026, "P")

        def http_get(url):
            if "100001" in url:
                raise ValueError("network boom")
            return b"%PDF-ok"

        summary = retrieve_documents(
            filings_repo=repo, state_repo=state, cache_dir=tmp_path,
            http_get=http_get, page_counter=lambda b: 1,
            filing_types=["P"], sleep_fn=NO_SLEEP,
        )

        assert (summary.fetched, summary.failed) == (1, 1)
        assert repo.rows["100001"].status == "failed"
        assert repo.rows["100001"].last_error
        assert repo.rows["100001"].fetch_attempts == 1
        assert repo.rows["100002"].status == "fetched"

    def test_allowlist_excludes_non_targets(self, fake_redis, tmp_path):
        state = StateRepository(fake_redis)
        repo = InMemoryRepo()
        repo.add("9001", 2026, "C")

        summary = retrieve_documents(
            filings_repo=repo, state_repo=state, cache_dir=tmp_path,
            http_get=lambda u: b"x", page_counter=lambda b: 1,
            filing_types=["P"], sleep_fn=NO_SLEEP,
        )
        assert summary.considered == 0
        assert repo.rows["9001"].status == "new"
