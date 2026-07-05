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

from app.config import settings
from app.db import get_engine
from app.dependencies import get_repo
from app.redis.repository import StateRepository
from app.repository.senate_filings import SenateFilingsRepository
from app.services.senate_efd import EfdSession
from app.services.senate_search import run_search_ingest_cycle

WORKER = "senate"
logger = logging.getLogger("quant_politicians.senate")


def run_cycle(state_repo: StateRepository, *, session=None, filings_repo=None, now=None) -> None:
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
        else:
            logger.info("no session/filings repo provided; heartbeat-only cycle")
    except Exception:
        state_repo.incr_counter(WORKER, "failed")
        logger.exception("senate cycle failed")
    state_repo.set_last_run(WORKER)


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
        run_cycle(state_repo, session=session, filings_repo=SenateFilingsRepository(engine))
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
