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

from app.config import settings
from app.db import get_engine
from app.dependencies import get_repo
from app.redis.repository import StateRepository
from app.repository.house_filings import HouseFilingsRepository
from app.services.house_index import run_house_ingest_cycle

WORKER = "house"
logger = logging.getLogger("quant_politicians.house")


def run_cycle(repo: StateRepository, *, filings_repo=None, download_fn=None, now=None):
    """Execute a single worker cycle.

    Writes a heartbeat, runs House index ingestion (Slice 1), and records the
    last-run timestamp. Ingestion is skipped with a warning when ``DATABASE_URL``
    is not configured so the worker still heartbeats in constrained environments.
    """
    repo.set_heartbeat(WORKER)
    summary = None
    if filings_repo is None:
        logger.info("no filings repository provided; heartbeat-only cycle")
    else:
        try:
            summary = run_house_ingest_cycle(
                filings_repo=filings_repo, state_repo=repo,
                download_fn=download_fn, now=now,
            )
            logger.info(
                "house cycle: seen=%d new=%d malformed=%d years=%s",
                summary.filings_seen, summary.new_filings,
                summary.malformed, summary.years_processed,
            )
        except Exception:
            repo.incr_counter(WORKER, "failed")
            logger.exception("house ingest cycle failed")
    repo.set_last_run(WORKER)
    return summary


def _build_filings_repo() -> HouseFilingsRepository | None:
    if not settings.database_url:
        logger.warning("DATABASE_URL not configured; ingestion disabled")
        return None
    return HouseFilingsRepository(get_engine())


def worker_loop(interval: int) -> None:
    logger.info("House worker starting (interval=%ds)", interval)
    while True:
        try:
            run_cycle(get_repo(), filings_repo=_build_filings_repo())
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
        run_cycle(get_repo(), filings_repo=_build_filings_repo())
        logger.info("Single pass complete")
    else:
        worker_loop(args.schedule)


if __name__ == "__main__":
    run_worker()
