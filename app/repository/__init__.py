"""Postgres repositories for durable disclosure state."""

from __future__ import annotations

from datetime import date, datetime


def jsonable(value):
    """Convert DB values (dates/datetimes) to JSON-serializable forms."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def row_to_dict(mapping) -> dict:
    return {key: jsonable(val) for key, val in mapping.items()}
