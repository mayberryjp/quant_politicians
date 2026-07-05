"""Redis key patterns for quant_politicians.

All keys are prefixed with ``qp:`` (quant-politicians), mirroring the ``qs:``
convention in ``quant_signals``. Keys are scoped per worker (``house`` / ``senate``).

Patterns
~~~~~~~~
<worker>:poll:heartbeat   -> ISO timestamp (TTL: heartbeat_ttl)
<worker>:poll:last_run    -> ISO timestamp
<worker>:poll:watermark   -> ISO date of the newest processed filing
<worker>:counter:<name>   -> integer counter
<worker>:lock:poll        -> single-flight worker lock
"""

from __future__ import annotations

PREFIX = "qp"


def _p(*parts: str) -> str:
    return f"{PREFIX}:{':'.join(parts)}"


def heartbeat_key(worker: str) -> str:
    return _p(worker, "poll", "heartbeat")


def last_run_key(worker: str) -> str:
    return _p(worker, "poll", "last_run")


def watermark_key(worker: str) -> str:
    return _p(worker, "poll", "watermark")


def counter_key(worker: str, name: str) -> str:
    return _p(worker, "counter", name)


def lock_key(worker: str) -> str:
    return _p(worker, "lock", "poll")


def backfill_done_key(worker: str) -> str:
    return _p(worker, "backfill", "done")


# Counter names surfaced by the House worker and the /stats endpoint.
HOUSE_COUNTERS = [
    "filings_seen",
    "new_filings",
    "malformed",
    "docs_fetched",
    "pages_rendered",
    "extraction_ok",
    "extraction_failed",
    "purchases_extracted",
    "signals_posted",
    "signals_duplicate",
    "signals_unresolved",
    "failed",
]
