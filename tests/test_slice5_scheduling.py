"""Slice 5 tests: single-flight lock, readiness staleness, safe re-runs."""

from __future__ import annotations

from app.config import settings
from app.redis import client as redis_client
from app.redis.repository import StateRepository
from app.services import house_worker


class TestLock:
    def test_single_flight(self, fake_redis):
        repo = StateRepository(fake_redis)
        assert repo.acquire_lock("house", 60) is True
        assert repo.acquire_lock("house", 60) is False  # already held
        repo.release_lock("house")
        assert repo.acquire_lock("house", 60) is True  # reacquire after release


class TestExecuteCycleLock:
    def test_skips_when_locked(self, fake_redis, monkeypatch):
        monkeypatch.setattr(redis_client, "_pool", fake_redis)
        monkeypatch.setattr(settings, "database_url", "")
        holder = StateRepository(fake_redis)
        assert holder.acquire_lock("house", 60) is True

        house_worker._execute_cycle()  # lock held -> should skip
        assert holder.get_heartbeat("house") is None

        holder.release_lock("house")
        house_worker._execute_cycle()  # now runs (heartbeat-only, no DB)
        assert holder.get_heartbeat("house") is not None

    def test_lock_released_after_cycle(self, fake_redis, monkeypatch):
        monkeypatch.setattr(redis_client, "_pool", fake_redis)
        monkeypatch.setattr(settings, "database_url", "")
        house_worker._execute_cycle()
        # lock must be free afterwards so the next scheduled tick can run
        assert StateRepository(fake_redis).acquire_lock("house", 60) is True


class TestReadiness:
    def test_degraded_without_heartbeat(self, app_client):
        resp = app_client.get("/politicians-cache/ready")
        assert resp.json["redis"] == "ok"
        assert resp.json["status"] == "degraded"  # no cycle -> heartbeat absent

    def test_ready_after_cycle(self, app_client, fake_redis):
        house_worker.run_cycle(StateRepository(fake_redis))
        resp = app_client.get("/politicians-cache/ready")
        assert resp.json["status"] == "ready"
