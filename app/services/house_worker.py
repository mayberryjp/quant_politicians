"""House financial-disclosure ingestion worker.

Slice 0 delivers the supervised long-lived worker skeleton: it wakes on a
schedule, writes a heartbeat, and records the last-run timestamp so readiness and
stats reflect a live worker. Later slices extend ``run_cycle`` with:

* Slice 1 - House index (ZIP/XML) ingestion + "since last run" + backfill
* Slice 2 - filing document (PDF) retrieval
* Slice 3 - vision-LLM OCR + structured extraction
* Slice 4 - purchase signal publishing to quant_signals
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
from app.repository.house_extractions import HouseExtractionsRepository
from app.repository.house_filings import HouseFilingsRepository
from app.services.house_documents import retrieve_documents
from app.services.house_extract import extract_documents
from app.services.house_index import run_house_ingest_cycle
from app.services.house_publish import publish_signals
from app.services.ollama_client import OllamaClient
from app.services.signals_client import SignalsClient

WORKER = "house"
logger = logging.getLogger("quant_politicians.house")


def run_cycle(
    state_repo: StateRepository,
    *,
    filings_repo=None,
    extractions_repo=None,
    llm=None,
    signals_client=None,
    download_fn=None,
    cache_dir=None,
    now=None,
):
    """Run one worker cycle: heartbeat -> ingest -> retrieve -> extract -> last_run.

    Each stage runs only when its dependency is provided; with no ``filings_repo``
    the cycle is heartbeat-only (constrained environments and Slice 0 tests).
    """
    state_repo.set_heartbeat(WORKER)
    cache_dir = Path(cache_dir or settings.doc_cache_dir)
    try:
        if filings_repo is None:
            logger.info("no filings repository provided; heartbeat-only cycle")
        else:
            ingest = run_house_ingest_cycle(
                filings_repo=filings_repo, state_repo=state_repo,
                download_fn=download_fn, now=now,
            )
            logger.info(
                "house ingest: seen=%d new=%d malformed=%d years=%s",
                ingest.filings_seen, ingest.new_filings, ingest.malformed, ingest.years_processed,
            )
            retrieval = retrieve_documents(
                filings_repo=filings_repo, state_repo=state_repo, cache_dir=cache_dir,
            )
            logger.info(
                "house retrieval: considered=%d fetched=%d failed=%d",
                retrieval.considered, retrieval.fetched, retrieval.failed,
            )
            if extractions_repo is not None and llm is not None:
                extraction = extract_documents(
                    filings_repo=filings_repo, extractions_repo=extractions_repo,
                    state_repo=state_repo, cache_dir=cache_dir, llm=llm,
                )
                logger.info(
                    "house extraction: docs=%d trades=%d failed=%d",
                    extraction.extracted_docs, extraction.trades, extraction.failed,
                )
            else:
                logger.info("extraction skipped (OLLAMA_MODEL / extractions repo unavailable)")
            if extractions_repo is not None and signals_client is not None:
                publish = publish_signals(
                    extractions_repo=extractions_repo, state_repo=state_repo,
                    signals_client=signals_client,
                )
                logger.info(
                    "house publish: considered=%d posted=%d duplicate=%d unresolved=%d failed=%d",
                    publish.considered, publish.posted, publish.duplicate,
                    publish.unresolved, publish.failed,
                )
            else:
                logger.info("publishing skipped (SIGNALS_API_URL unavailable)")
    except Exception:
        state_repo.incr_counter(WORKER, "failed")
        logger.exception("house cycle failed")
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
    if not settings.database_url:
        logger.warning("DATABASE_URL not configured; ingestion disabled")
        run_cycle(state_repo)
        return
    engine = get_engine()
    run_cycle(
        state_repo,
        filings_repo=HouseFilingsRepository(engine),
        extractions_repo=HouseExtractionsRepository(engine),
        llm=_build_llm(),
        signals_client=_build_signals_client(),
        cache_dir=Path(settings.doc_cache_dir),
    )


def worker_loop(interval: int) -> None:
    logger.info("House worker starting (interval=%ds)", interval)
    while True:
        try:
            _execute_cycle()
        except Exception:
            logger.exception("House cycle failed - will retry next cycle")
        time.sleep(interval)


def run_worker() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    parser = argparse.ArgumentParser(description="House disclosure ingestion worker")
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
