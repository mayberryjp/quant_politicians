"""House annual FD index ingestion (Slice 1).

Pipeline for one worker cycle:

1. Resolve the target years from ``HOUSE_FD_YEARS`` + ``BACKFILL_YEARS`` (minus
   backfill years already completed).
2. For each year, download ``{YEAR}FD.zip`` (streamed, size-capped), extract
   ``{YEAR}FD.xml``, and parse the ``<Member>`` rows.
3. Upsert every row into ``house_filings`` (idempotent by ``doc_id``) and count
   the ones not previously seen ("new since last run").
4. Record the newest ``FilingDate`` as the Redis watermark and mark completed
   backfill years so they are not re-downloaded.

All XML is parsed with ``defusedxml`` to avoid XXE / entity-expansion attacks.
Network and persistence are injected so the logic is unit-testable without live
services. The ``FilingType`` allowlist is intentionally NOT applied here - the
full index is stored; targeting happens at retrieval time (Slice 2).
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import httpx
from defusedxml.ElementTree import fromstring as safe_fromstring

from app.config import settings
from app.models.house import HouseFiling

WORKER = "house"
HOUSE_DATA_START_YEAR = 2008

logger = logging.getLogger("quant_politicians.house.index")


# ---------------------------------------------------------------------------
# Parsing helpers (pure)
# ---------------------------------------------------------------------------

def parse_filing_date(value: str | None) -> date | None:
    """Parse the House ``M/D/YYYY`` date format (leading zeros optional)."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_index_xml(xml_bytes: bytes) -> tuple[list[HouseFiling], int]:
    """Parse ``{YEAR}FD.xml`` into filings + a count of malformed rows.

    A row is malformed (skipped + counted) when it lacks a usable ``DocID`` or a
    numeric ``Year`` - without those it cannot be deduped, stored, or retrieved.
    """
    root = safe_fromstring(xml_bytes)
    filings: list[HouseFiling] = []
    malformed = 0

    def _text(el, tag: str) -> str:
        return (el.findtext(tag) or "").strip()

    for member in root.findall(".//Member"):
        doc_id = _text(member, "DocID")
        year_txt = _text(member, "Year")
        if not doc_id or not year_txt.isdigit():
            malformed += 1
            continue
        filings.append(
            HouseFiling(
                doc_id=doc_id,
                prefix=_text(member, "Prefix") or None,
                last=_text(member, "Last"),
                first=_text(member, "First"),
                suffix=_text(member, "Suffix") or None,
                filing_type=_text(member, "FilingType"),
                state_dst=_text(member, "StateDst") or None,
                year=int(year_txt),
                filing_date=parse_filing_date(member.findtext("FilingDate")),
            )
        )
    return filings, malformed


def extract_index_xml(zip_bytes: bytes, year: int, max_bytes: int) -> bytes:
    """Extract ``{YEAR}FD.xml`` from the annual ZIP with a zip-bomb size guard."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        wanted = f"{year}fd.xml"
        target = next((n for n in names if n.lower() == wanted), None)
        if target is None:
            target = next((n for n in names if n.lower().endswith(".xml")), None)
        if target is None:
            raise ValueError(f"no XML entry in index zip for {year}")
        if zf.getinfo(target).file_size > max_bytes:
            raise ValueError(f"index xml for {year} exceeds MAX_DOC_BYTES")
        with zf.open(target) as handle:
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"index xml for {year} exceeds MAX_DOC_BYTES")
        return data


# ---------------------------------------------------------------------------
# Download (network - injected in tests)
# ---------------------------------------------------------------------------

def download_index_zip(year: int) -> bytes | None:
    """Stream ``{YEAR}FD.zip`` with a polite UA and a size cap; None on failure."""
    url = f"{settings.house_fd_base_url}/public_disc/financial-pdfs/{year}FD.zip"
    headers = {"User-Agent": settings.http_user_agent}
    try:
        with httpx.stream(
            "GET", url, headers=headers,
            timeout=httpx.Timeout(60.0), follow_redirects=True,
        ) as resp:
            resp.raise_for_status()
            buf = bytearray()
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
                if len(buf) > settings.max_doc_bytes:
                    raise ValueError(f"index zip for {year} exceeds MAX_DOC_BYTES")
            return bytes(buf)
    except Exception:
        logger.exception("failed to download index zip for %s", year)
        return None


# ---------------------------------------------------------------------------
# Year resolution
# ---------------------------------------------------------------------------

def _parse_year_list(value: str | None) -> set[int]:
    out: set[int] = set()
    for part in (value or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def resolve_target_years(
    current_year: int,
    house_fd_years: str,
    backfill_years: str,
    done_backfill: set[str],
) -> list[int]:
    """Determine which years to process this cycle.

    Regular years (``HOUSE_FD_YEARS``) are always processed. Backfill years are
    added only until they have been completed once (tracked in ``done_backfill``).
    """
    years: set[int] = set()
    hfy = (house_fd_years or "current").strip().lower()
    if hfy == "current":
        years.add(current_year)
    elif hfy == "all":
        years.update(range(HOUSE_DATA_START_YEAR, current_year + 1))
    else:
        years.update(_parse_year_list(house_fd_years))

    for year in _parse_year_list(backfill_years):
        if str(year) not in done_backfill:
            years.add(year)
    return sorted(years)


# ---------------------------------------------------------------------------
# Ingestion orchestration
# ---------------------------------------------------------------------------

@dataclass
class IngestSummary:
    filings_seen: int = 0
    new_filings: int = 0
    malformed: int = 0
    year_failures: int = 0
    years_processed: list[int] = field(default_factory=list)


def run_ingest(*, filings_repo, state_repo, download_fn, years: list[int]) -> IngestSummary:
    """Download, parse, and upsert filings for each target year."""
    summary = IngestSummary()
    overall_max: date | None = None

    for year in years:
        try:
            zip_bytes = download_fn(year)
            if not zip_bytes:
                summary.year_failures += 1
                logger.warning("no index zip for %s; skipping", year)
                continue
            xml_bytes = extract_index_xml(zip_bytes, year, settings.max_doc_bytes)
            filings, malformed = parse_index_xml(xml_bytes)
            summary.malformed += malformed

            seen = filings_repo.get_seen_doc_ids(year)
            new_count = sum(1 for f in filings if f.doc_id not in seen)
            summary.filings_seen += len(filings)
            summary.new_filings += new_count

            filings_repo.upsert_many(filings)

            year_max = max((f.filing_date for f in filings if f.filing_date), default=None)
            if year_max and (overall_max is None or year_max > overall_max):
                overall_max = year_max

            summary.years_processed.append(year)
            logger.info(
                "year %s: %d filings (%d new, %d malformed)",
                year, len(filings), new_count, malformed,
            )
        except Exception:
            summary.year_failures += 1
            logger.exception("failed to ingest House index for year %s", year)

    if overall_max:
        state_repo.set_watermark(WORKER, overall_max.isoformat())
    state_repo.incr_counter(WORKER, "filings_seen", summary.filings_seen)
    state_repo.incr_counter(WORKER, "new_filings", summary.new_filings)
    state_repo.incr_counter(WORKER, "malformed", summary.malformed)
    return summary


def run_house_ingest_cycle(*, filings_repo, state_repo, download_fn=None, now=None) -> IngestSummary:
    """Resolve target years, ingest them, and mark completed backfill years."""
    now = now or datetime.now(timezone.utc)
    download_fn = download_fn or download_index_zip
    done = state_repo.get_backfill_done(WORKER)
    years = resolve_target_years(now.year, settings.house_fd_years, settings.backfill_years, done)
    logger.info("House ingest: target years=%s (backfill done=%s)", years, sorted(done))

    summary = run_ingest(
        filings_repo=filings_repo, state_repo=state_repo,
        download_fn=download_fn, years=years,
    )

    backfill_set = _parse_year_list(settings.backfill_years)
    for year in summary.years_processed:
        if year in backfill_set:
            state_repo.mark_backfill_done(WORKER, year)
    return summary
