"""Senate Slice 0 tests: worker skeleton heartbeat, stats/readiness include senate, config."""

from __future__ import annotations

from app.config import settings
from app.redis.repository import StateRepository
from app.services import senate_worker


class TestSenateWorker:
    def test_run_cycle_sets_heartbeat(self, fake_redis):
        repo = StateRepository(fake_redis)
        assert repo.get_heartbeat("senate") is None
        senate_worker.run_cycle(repo)
        assert repo.get_heartbeat("senate") is not None
        assert repo.get_last_run("senate") is not None


class TestStatsAndReadiness:
    def test_stats_has_senate(self, app_client):
        resp = app_client.get("/politicians-cache/stats")
        assert "senate" in resp.json
        assert "reports_seen" in resp.json["senate"]

    def test_ready_lists_senate_heartbeat(self, app_client):
        resp = app_client.get("/politicians-cache/ready")
        assert "senate" in resp.json["heartbeats"]


class TestSenateConfig:
    def test_defaults(self):
        assert settings.senate_efd_base_url.startswith("https://efdsearch.senate.gov")
        assert settings.senate_filer_types == "all"


class TestExecuteCycleLock:
    def test_skips_when_locked(self, fake_redis, monkeypatch):
        from app.redis import client as redis_client
        monkeypatch.setattr(redis_client, "_pool", fake_redis)
        holder = StateRepository(fake_redis)
        assert holder.acquire_lock("senate", 60) is True

        senate_worker._execute_cycle()  # lock held -> skip
        assert holder.get_heartbeat("senate") is None

        holder.release_lock("senate")
        senate_worker._execute_cycle()  # runs
        assert holder.get_heartbeat("senate") is not None
