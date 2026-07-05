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
from app.dependencies import get_repo
from app.redis.repository import StateRepository

WORKER = "senate"
logger = logging.getLogger("quant_politicians.senate")


def run_cycle(state_repo: StateRepository) -> None:
    """Slice 0: heartbeat-only cycle. Later slices extend the pipeline."""
    state_repo.set_heartbeat(WORKER)
    logger.info("senate worker cycle complete (scaffold no-op)")
    state_repo.set_last_run(WORKER)


def _execute_cycle() -> None:
    state_repo = get_repo()
    if not state_repo.acquire_lock(WORKER, settings.lock_ttl):
        logger.warning("another senate cycle holds the lock; skipping this tick")
        return
    try:
        run_cycle(state_repo)
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
