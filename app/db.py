"""Database engine helpers (mirrors quant_signals)."""

from __future__ import annotations

import os

from sqlalchemy import Engine
from sqlalchemy import create_engine as _create_engine

from app.config import settings


def container_timezone() -> str:
    """Resolve the container's local IANA timezone name.

    Prefers the ``TZ`` environment variable, then the ``/etc/localtime`` symlink
    target, falling back to UTC. This is the timezone the database session is
    pinned to so all timestamps use the container's local time.
    """
    tz = os.environ.get("TZ")
    if tz:
        return tz
    marker = "zoneinfo/"
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        target = ""
    if marker in target:
        return target.split(marker, 1)[1]
    return "UTC"


def get_engine() -> Engine:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return _create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"options": f"-c timezone={container_timezone()}"},
    )
