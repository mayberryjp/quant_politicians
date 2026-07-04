"""Shared fixtures for the quant_politicians test suite (mirrors quant_signals)."""

from __future__ import annotations

import fakeredis
import pytest
from webtest import TestApp

from app.redis import client as redis_client
from app.redis.repository import StateRepository


@pytest.fixture
def fake_redis():
    """Provide a fresh fakeredis instance per test."""
    r = fakeredis.FakeRedis(decode_responses=True)
    yield r
    r.close()


@pytest.fixture
def repo(fake_redis) -> StateRepository:
    return StateRepository(fake_redis)


@pytest.fixture
def app_client(fake_redis) -> TestApp:
    """HTTP test client with the Redis dependency overridden by fakeredis."""
    original = redis_client._pool
    redis_client._pool = fake_redis
    from app.main import app

    test_app = TestApp(app)
    yield test_app
    redis_client._pool = original
