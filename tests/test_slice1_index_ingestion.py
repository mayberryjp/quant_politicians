"""Slice 1 tests: House index parsing, year resolution, ingestion, and backfill."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

from app.config import settings
from app.redis.repository import StateRepository
from app.services import house_index
from app.services.house_index import (
    extract_index_xml,
    parse_filing_date,
    parse_index_xml,
    resolve_target_years,
    run_house_ingest_cycle,
    run_ingest,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def xml_for_year(year: int) -> bytes:
    """Two valid <Member> rows + one malformed (empty DocID) for a given year."""
    return (
        f"""<?xml version="1.0"?>
<FinancialDisclosure>
  <Member><Prefix/><Last>Aaron</Last><First>Richard</First><Suffix/>
    <FilingType>P</FilingType><StateDst>MI04</StateDst><Year>{year}</Year>
    <FilingDate>4/15/{year}</FilingDate><DocID>{year}001</DocID></Member>
  <Member><Prefix/><Last>Abdulle</Last><First>Abdisallam</First><Suffix/>
    <FilingType>C</FilingType><StateDst>MN02</StateDst><Year>{year}</Year>
    <FilingDate>6/11/{year}</FilingDate><DocID>{year}002</DocID></Member>
  <Member><Prefix/><Last>NoDoc</Last><First>Bad</First><Suffix/>
    <FilingType>P</FilingType><StateDst>CA01</StateDst><Year>{year}</Year>
    <FilingDate>1/2/{year}</FilingDate><DocID></DocID></Member>
</FinancialDisclosure>
"""
    ).encode()


def build_zip(year: int, xml: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{year}FD.xml", xml or xml_for_year(year))
    return buf.getvalue()


class InMemoryFilingsRepo:
    """Duck-typed stand-in for HouseFilingsRepository (no Postgres needed)."""

    def __init__(self) -> None:
        self.rows: dict[str, object] = {}

    def get_seen_doc_ids(self, year: int) -> set[str]:
        return {doc_id for doc_id, f in self.rows.items() if f.year == year}

    def upsert_many(self, filings) -> None:
        for f in filings:
            self.rows[f.doc_id] = f


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestParsing:
    def test_parse_filing_date_mdyyyy(self):
        d = parse_filing_date("4/15/2026")
        assert (d.year, d.month, d.day) == (2026, 4, 15)

    def test_parse_filing_date_invalid(self):
        assert parse_filing_date("") is None
        assert parse_filing_date("not-a-date") is None

    def test_parse_index_xml_counts(self):
        filings, malformed = parse_index_xml(xml_for_year(2026))
        assert len(filings) == 2
        assert malformed == 1
        assert {f.doc_id for f in filings} == {"2026001", "2026002"}
        assert {f.filing_type for f in filings} == {"P", "C"}

    def test_parse_index_xml_dates(self):
        filings, _ = parse_index_xml(xml_for_year(2026))
        by_id = {f.doc_id: f for f in filings}
        assert by_id["2026001"].filing_date.isoformat() == "2026-04-15"

    def test_extract_index_xml(self):
        xml = extract_index_xml(build_zip(2026), 2026, settings.max_doc_bytes)
        assert b"FinancialDisclosure" in xml


# ---------------------------------------------------------------------------
# Year resolution
# ---------------------------------------------------------------------------

class TestYearResolution:
    def test_current(self):
        assert resolve_target_years(2026, "current", "", set()) == [2026]

    def test_explicit_list(self):
        assert resolve_target_years(2026, "2024,2025", "", set()) == [2024, 2025]

    def test_all(self):
        years = resolve_target_years(2026, "all", "", set())
        assert years[0] == 2008 and years[-1] == 2026

    def test_backfill_added_then_excluded(self):
        assert resolve_target_years(2026, "current", "2023", set()) == [2023, 2026]
        assert resolve_target_years(2026, "current", "2023", {"2023"}) == [2026]


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

class TestIngest:
    def test_idempotent_upsert_and_since_last_run(self, fake_redis):
        state = StateRepository(fake_redis)
        repo = InMemoryFilingsRepo()

        first = run_ingest(filings_repo=repo, state_repo=state, download_fn=build_zip, years=[2026])
        assert first.filings_seen == 2
        assert first.new_filings == 2
        assert first.malformed == 1

        second = run_ingest(filings_repo=repo, state_repo=state, download_fn=build_zip, years=[2026])
        assert second.filings_seen == 2
        assert second.new_filings == 0  # nothing new on a second run
        assert len(repo.rows) == 2

    def test_watermark_is_newest_filing_date(self, fake_redis):
        state = StateRepository(fake_redis)
        repo = InMemoryFilingsRepo()
        run_ingest(filings_repo=repo, state_repo=state, download_fn=build_zip, years=[2026])
        assert state.get_watermark("house") == "2026-06-11"

    def test_download_failure_is_isolated(self, fake_redis):
        state = StateRepository(fake_redis)
        repo = InMemoryFilingsRepo()

        def dl(year):
            return None if year == 2025 else build_zip(year)

        summary = run_ingest(filings_repo=repo, state_repo=state, download_fn=dl, years=[2025, 2026])
        assert summary.year_failures == 1
        assert summary.years_processed == [2026]
        assert summary.new_filings == 2


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

class TestBackfill:
    def test_backfill_processes_once_then_noop(self, fake_redis, monkeypatch):
        monkeypatch.setattr(settings, "house_fd_years", "current")
        monkeypatch.setattr(settings, "backfill_years", "2023")
        state = StateRepository(fake_redis)
        repo = InMemoryFilingsRepo()
        now = datetime(2026, 7, 5, tzinfo=timezone.utc)

        calls: list[int] = []

        def dl(year):
            calls.append(year)
            return build_zip(year)

        first = run_house_ingest_cycle(filings_repo=repo, state_repo=state, download_fn=dl, now=now)
        assert set(first.years_processed) == {2023, 2026}
        assert 2023 in calls

        calls.clear()
        second = run_house_ingest_cycle(filings_repo=repo, state_repo=state, download_fn=dl, now=now)
        assert 2026 in second.years_processed
        assert 2023 not in second.years_processed  # backfill completed
        assert 2023 not in calls  # not re-downloaded
