"""Senate eFD ingestion worker.

Slice 0 delivers the supervised long-lived worker skeleton (heartbeat-only), a
sibling to the House worker under supervisord. Later slices extend the cycle:

* Slice 1 - eFD session + mandatory agreement handshake
* Slice 2 - search-endpoint ingestion + backfill + "since last run"
* Slice 3 - report retrieval (electronic HTML + paper PDF)
* Slice 4 - content capture + shared vision-LLM extraction (+ HTML ticker cross-check)
* Slice 5 - purchase signal publishing to quant_signals

Reuses the shared core (StateRepository, single-flight lock, extractor, publisher).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from app.config import settings
from app.db import get_engine
from app.dependencies import get_repo
from app.redis.repository import StateRepository
from app.repository.senate_extractions import SenateExtractionsRepository
from app.repository.senate_filings import SenateFilingsRepository
from app.services.senate_documents import retrieve_reports
from app.services.senate_efd import EfdSession
from app.services.senate_extract import extract_reports
from app.services.senate_publish import publish_signals
from app.services.senate_search import run_search_ingest_cycle
from app.services.ollama_client import OllamaClient
from app.services.signals_client import SignalsClient

WORKER = "senate"
logger = logging.getLogger("quant_politicians.senate")


def run_cycle(state_repo: StateRepository, *, session=None, filings_repo=None, extractions_repo=None, llm=None, signals_client=None, now=None) -> None:
    """Heartbeat, then (when a session + filings repo are provided) run eFD search
    ingestion. With no deps the cycle is heartbeat-only (Slice 0 behavior / no DB)."""
    state_repo.set_heartbeat(WORKER)
    try:
        if session is not None and filings_repo is not None:
            summary = run_search_ingest_cycle(
                session=session, filings_repo=filings_repo, state_repo=state_repo, now=now,
            )
            logger.info(
                "senate search: seen=%d new=%d backfill=%s window_start=%s",
                summary.seen, summary.new, summary.is_backfill, summary.start_date,
            )
            retrieval = retrieve_reports(
                session=session, filings_repo=filings_repo, state_repo=state_repo,
                cache_dir=Path(settings.doc_cache_dir),
            )
            logger.info(
                "senate retrieval: considered=%d fetched=%d failed=%d",
                retrieval.considered, retrieval.fetched, retrieval.failed,
            )
            if extractions_repo is not None and llm is not None:
                extraction = extract_reports(
                    filings_repo=filings_repo, extractions_repo=extractions_repo,
                    state_repo=state_repo, cache_dir=Path(settings.doc_cache_dir), llm=llm,
                )
                logger.info(
                    "senate extraction: docs=%d trades=%d purchases=%d pages=%d mismatches=%d failed=%d",
                    extraction.extracted_docs, extraction.trades, extraction.purchases,
                    extraction.pages_rendered, extraction.mismatches, extraction.failed,
                )
            if extractions_repo is not None and signals_client is not None:
                publish = publish_signals(
                    extractions_repo=extractions_repo, state_repo=state_repo,
                    signals_client=signals_client,
                )
                logger.info(
                    "senate publish: considered=%d posted=%d duplicate=%d unresolved=%d failed=%d",
                    publish.considered, publish.posted, publish.duplicate,
                    publish.unresolved, publish.failed,
                )
        else:
            logger.info("no session/filings repo provided; heartbeat-only cycle")
    except Exception:
        state_repo.incr_counter(WORKER, "failed")
        logger.exception("senate cycle failed")
    state_repo.set_last_run(WORKER)


def _build_llm():
    if not settings.ollama_model:
        logger.warning("OLLAMA_MODEL not set; extraction disabled")
        return None
    return OllamaClient(settings.ollama_url, settings.ollama_model, settings.ollama_timeout)


def _build_signals_client():
    if not settings.signals_api_url:
        logger.warning("SIGNALS_API_URL not set; publishing disabled")
        return None
    return SignalsClient(settings.signals_api_url, settings.signals_timeout)


def _execute_cycle() -> None:
    state_repo = get_repo()
    if not state_repo.acquire_lock(WORKER, settings.lock_ttl):
        logger.warning("another senate cycle holds the lock; skipping this tick")
        return
    try:
        if not settings.database_url:
            logger.warning("DATABASE_URL not configured; ingestion disabled")
            run_cycle(state_repo)
            return
        engine = get_engine()
        session = EfdSession(state_repo=state_repo)
        run_cycle(
            state_repo, session=session,
            filings_repo=SenateFilingsRepository(engine),
            extractions_repo=SenateExtractionsRepository(engine),
            llm=_build_llm(),
            signals_client=_build_signals_client(),
        )
    finally:
        state_repo.release_lock(WORKER)


def worker_loop(interval: int) -> None:
    logger.info("Senate worker starting (interval=%ds)", interval)
    while True:
        try:
            _execute_cycle()
        except Exception:
            logger.exception("Senate cycle failed - will retry next cycle")
        time.sleep(interval)


def run_worker() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    parser = argparse.ArgumentParser(description="Senate disclosure ingestion worker")
    parser.add_argument(
        "--schedule", type=int, default=settings.poll_interval,
        help="Seconds between cycles",
    )
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    args = parser.parse_args()

    if args.once:
        _execute_cycle()
        logger.info("Single pass complete")
    else:
        worker_loop(args.schedule)


if __name__ == "__main__":
    run_worker()
