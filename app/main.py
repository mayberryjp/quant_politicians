"""Bottle application entry point (mirrors quant_signals)."""

from __future__ import annotations

import atexit
import logging
import sys

from bottle import Bottle

from app.config import settings
from app.redis.client import close_redis
from app.routes import health, house

SERVICE_NAME = "quant-politicians-api"
log = logging.getLogger(SERVICE_NAME)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
    force=True,
)

app = Bottle()
app.merge(health.sub)
app.merge(house.sub)

atexit.register(close_redis)

if __name__ == "__main__":
    from waitress import serve

    log.info(
        "Starting quant-politicians API on %s:%d...",
        settings.api_listen_address,
        settings.api_port,
    )
    serve(app, host=settings.api_listen_address, port=settings.api_port, threads=20)
