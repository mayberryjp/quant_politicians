"""Dependency helpers (mirrors quant_signals)."""

from __future__ import annotations

from app.redis.client import get_redis
from app.redis.repository import StateRepository


def get_repo() -> StateRepository:
    return StateRepository(get_redis())
