"""Dependency helpers (mirrors quant_signals)."""

from __future__ import annotations

from app.db import get_engine
from app.redis.client import get_redis
from app.redis.repository import StateRepository
from app.repository.house_extractions import HouseExtractionsRepository
from app.repository.house_filings import HouseFilingsRepository
from app.repository.senate_extractions import SenateExtractionsRepository
from app.repository.senate_filings import SenateFilingsRepository


def get_repo() -> StateRepository:
    return StateRepository(get_redis())


def get_filings_repo() -> HouseFilingsRepository:
    return HouseFilingsRepository(get_engine())


def get_extractions_repo() -> HouseExtractionsRepository:
    return HouseExtractionsRepository(get_engine())


def get_senate_filings_repo() -> SenateFilingsRepository:
    return SenateFilingsRepository(get_engine())


def get_senate_extractions_repo() -> SenateExtractionsRepository:
    return SenateExtractionsRepository(get_engine())
