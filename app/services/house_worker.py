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
from app.dependencies import get_repo
from app.redis.repository import StateRepository

WORKER = "house"
logger = logging.getLogger("quant_politicians.house")


def run_cycle(repo: StateRepository) -> None:
    """Execute a single worker cycle.

    Slice 0: no ingestion yet - record heartbeat + last-run. Subsequent slices
    extend this pipeline.
    """
    repo.set_heartbeat(WORKER)
    repo.set_last_run(WORKER)
    logger.info("house worker cycle complete (scaffold no-op)")


def worker_loop(interval: int) -> None:
    logger.info("House worker starting (interval=%ds)", interval)
    while True:
        try:
            run_cycle(get_repo())
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
        run_cycle(get_repo())
        logger.info("Single pass complete")
    else:
        worker_loop(args.schedule)


if __name__ == "__main__":
    run_worker()
