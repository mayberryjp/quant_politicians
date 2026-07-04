"""Database engine helpers (mirrors quant_signals)."""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy import create_engine as _create_engine

from app.config import settings


def get_engine() -> Engine:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return _create_engine(settings.database_url, pool_pre_ping=True)
