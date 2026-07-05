"""HTTP client for the quant_signals producer API (Slice 4)."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("quant_politicians.signals")


class SignalsClient:
    """Posts ticker-level signals to the quant_signals watchlist service."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def post_signal(self, payload: dict) -> dict:
        """POST /signals; returns the response JSON (contains ``status``)."""
        resp = httpx.post(f"{self.base_url}/signals", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
