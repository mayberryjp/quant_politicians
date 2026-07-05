"""Slice 0 tests: scaffold health/readiness/stats, config, and worker heartbeat."""

from __future__ import annotations

from app.config import settings
from app.redis.repository import StateRepository
from app.services import house_worker


class TestHealth:
    def test_health_ok(self, app_client):
        resp = app_client.get("/politicians-cache/health")
        assert resp.status_int == 200
        assert resp.json["status"] == "ok"

    def test_readiness_ok(self, app_client):
        resp = app_client.get("/politicians-cache/ready")
        assert resp.status_int == 200
        # No cycle has run yet -> readiness is "degraded" (redis ok, heartbeat absent).
        assert resp.json["status"] in ("ready", "degraded")
        assert resp.json["redis"] == "ok"
        assert "house" in resp.json["heartbeats"]

    def test_stats_shape(self, app_client):
        resp = app_client.get("/politicians-cache/stats")
        assert resp.status_int == 200
        assert "house" in resp.json
        assert "filings_seen" in resp.json["house"]


class TestConfig:
    def test_defaults(self):
        assert settings.house_fd_base_url.startswith("https://disclosures-clerk.house.gov")
        assert settings.target_filing_types == "P"
        assert settings.publish_transaction_types == "purchase"
        assert settings.signals_source_name == "house-disclosures-v1"

    def test_parsers(self):
        assert settings.parsed_target_filing_types() == ["P"]
        assert settings.parsed_publish_transaction_types() == ["purchase"]


class TestWorker:
    def test_run_cycle_sets_heartbeat(self, fake_redis):
        repo = StateRepository(fake_redis)
        assert repo.get_heartbeat("house") is None
        house_worker.run_cycle(repo)
        assert repo.get_heartbeat("house") is not None
        assert repo.get_last_run("house") is not None

    def test_ready_reflects_heartbeat_after_cycle(self, app_client, fake_redis):
        house_worker.run_cycle(StateRepository(fake_redis))
        resp = app_client.get("/politicians-cache/ready")
        assert resp.json["heartbeats"]["house"] is not None
        assert resp.json["status"] == "ready"
