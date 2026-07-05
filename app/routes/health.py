"""Health, readiness, and operational visibility routes (Slice 0)."""

from __future__ import annotations

from bottle import Bottle

from app.dependencies import get_repo
from app.redis import keys

sub = Bottle()

WORKERS = ["house"]


@sub.get("/politicians-cache/health")
def health():
    return {"status": "ok"}


@sub.get("/politicians-cache/ready")
def readiness():
    repo = get_repo()
    try:
        redis_ok = repo.r.ping()
    except Exception:
        redis_ok = False
    heartbeats = {w: repo.get_heartbeat(w) for w in WORKERS} if redis_ok else {}
    workers_healthy = redis_ok and all(heartbeats.get(w) for w in WORKERS)
    if not redis_ok:
        status = "not_ready"
    elif workers_healthy:
        status = "ready"
    else:
        status = "degraded"
    return {
        "status": status,
        "redis": "ok" if redis_ok else "unavailable",
        "heartbeats": heartbeats,
    }


@sub.get("/politicians-cache/stats")
def stats():
    repo = get_repo()
    house = repo.get_counters("house", keys.HOUSE_COUNTERS)
    house["last_run"] = repo.get_last_run("house")
    house["heartbeat"] = repo.get_heartbeat("house")
    return {"house": house}
