"""Redis repository for worker run-state, heartbeats, and counters.

Slice 0 scope: coordination primitives shared by the ingestion workers. Domain
persistence (filings/extractions) lives in Postgres and is added in later slices.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import redis as redis_lib

from app.config import settings
from app.redis import keys


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateRepository:
    """Encapsulates Redis interactions for worker coordination and stats."""

    def __init__(self, redis: redis_lib.Redis) -> None:
        self.r = redis

    # --- Heartbeat / run-state ---
    def set_heartbeat(self, worker: str) -> None:
        self.r.set(keys.heartbeat_key(worker), _now_iso(), ex=settings.heartbeat_ttl)

    def get_heartbeat(self, worker: str) -> str | None:
        return self.r.get(keys.heartbeat_key(worker))

    def set_last_run(self, worker: str) -> None:
        self.r.set(keys.last_run_key(worker), _now_iso())

    def get_last_run(self, worker: str) -> str | None:
        return self.r.get(keys.last_run_key(worker))

    def set_watermark(self, worker: str, value: str) -> None:
        self.r.set(keys.watermark_key(worker), value)

    def get_watermark(self, worker: str) -> str | None:
        return self.r.get(keys.watermark_key(worker))

    # --- Counters ---
    def incr_counter(self, worker: str, name: str, amount: int = 1) -> int:
        return self.r.incrby(keys.counter_key(worker, name), amount)

    def get_counters(self, worker: str, names: list[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for name in names:
            val = self.r.get(keys.counter_key(worker, name))
            result[name] = int(val) if val else 0
        return result

    # --- Backfill tracking ---
    def get_backfill_done(self, worker: str) -> set[str]:
        return set(self.r.smembers(keys.backfill_done_key(worker)))

    def mark_backfill_done(self, worker: str, year: int) -> None:
        self.r.sadd(keys.backfill_done_key(worker), str(year))

    # --- Session persistence (eFD) ---
    def set_session(self, worker: str, data: dict, ttl: int = 1800) -> None:
        self.r.set(keys.session_key(worker), json.dumps(data), ex=ttl)

    def get_session(self, worker: str) -> dict | None:
        raw = self.r.get(keys.session_key(worker))
        return json.loads(raw) if raw else None

    # --- Single-flight lock (used from Slice 6 onward) ---
    def acquire_lock(self, worker: str, ttl: int) -> bool:
        return bool(self.r.set(keys.lock_key(worker), _now_iso(), nx=True, ex=ttl))

    def release_lock(self, worker: str) -> None:
        self.r.delete(keys.lock_key(worker))
