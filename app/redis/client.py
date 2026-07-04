"""Thin sync Redis client wrapper with health helpers (mirrors quant_signals)."""

from __future__ import annotations

import redis as redis_lib

from app.config import settings

_pool: redis_lib.Redis | None = None


def get_redis() -> redis_lib.Redis:
    global _pool
    if _pool is None:
        _pool = redis_lib.from_url(settings.redis_url, decode_responses=True)
    return _pool


def close_redis() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def ping_redis() -> bool:
    try:
        return get_redis().ping()
    except Exception:
        return False
