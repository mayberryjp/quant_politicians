"""Minimal Ollama HTTP client for JSON extraction from images (Slice 3)."""

from __future__ import annotations

import base64
import json
import logging

import httpx

logger = logging.getLogger("quant_politicians.ollama")


class OllamaClient:
    """Calls a vision-capable Ollama model and returns parsed JSON output."""

    def __init__(self, base_url: str, model: str, timeout: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate_json(self, prompt: str, images: list[bytes]) -> dict:
        """POST to /api/generate with base64 images and ``format=json``.

        Returns the parsed JSON object from the model's ``response`` string.
        Raises on transport errors or invalid JSON (the caller retries).
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [base64.b64encode(img).decode("ascii") for img in images],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0},
        }
        resp = httpx.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return json.loads(data.get("response", ""))
