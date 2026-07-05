"""Publish extracted purchases as signals to quant_signals (Slice 4).

Only ``purchase`` transactions with a valid ticker are published (``direction=long``).
Each extraction maps to one signal with a deterministic idempotency key
(``{source}:{doc_id}:{TICKER}``); permanent dedup comes from the ``published`` flag,
and the watchlist service's 24h idempotency window is a second guard. Response
states (``accepted`` / ``duplicate`` / ``unresolved``) are recorded and counted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings
from app.models.house import PublishableExtraction

WORKER = "house"
logger = logging.getLogger("quant_politicians.house.publish")


def build_signal_payload(row: PublishableExtraction, source: str) -> dict:
    who = f"Rep. {row.first} {row.last}".strip()
    state = f" ({row.state_dst})" if row.state_dst else ""
    when = row.transaction_date or "an unknown date"
    amount = f" ({row.amount_range})" if row.amount_range else ""
    reason = (
        f"{who}{state} disclosed a purchase of {row.ticker}{amount} on {when}. "
        f"House PTR DocID {row.doc_id}, filed {row.filing_date}."
    )
    tags = ["congress", "house", "ptr", "purchase"]
    if row.state_dst:
        tags.append(row.state_dst[:2].lower())
    return {
        "source": source,
        "idempotency_key": f"{source}:{row.doc_id}:{row.ticker}",
        "ticker": row.ticker,
        "reason": reason[:2000],
        "market": settings.signal_market,
        "locale": settings.signal_locale,
        "signal_type": settings.signal_type,
        "direction": "long",
        "tags": tags,
        "metadata": {
            "doc_id": row.doc_id,
            "filing_date": row.filing_date,
            "member": {"first": row.first, "last": row.last, "state_dst": row.state_dst},
            "transaction": {
                "type": row.transaction_type,
                "date": row.transaction_date,
                "amount_range": row.amount_range,
                "owner": row.owner,
            },
            "llm_model": row.llm_model,
            "schema_version": 1,
        },
    }


@dataclass
class PublishSummary:
    considered: int = 0
    posted: int = 0
    duplicate: int = 0
    unresolved: int = 0
    other: int = 0
    failed: int = 0


_STATUS_COUNTER = {
    "accepted": "signals_posted",
    "duplicate": "signals_duplicate",
    "unresolved": "signals_unresolved",
}


def publish_signals(
    *,
    extractions_repo,
    state_repo,
    signals_client,
    publish_types=None,
    source_name=None,
    limit=None,
) -> PublishSummary:
    publish_types = publish_types if publish_types is not None else settings.parsed_publish_transaction_types()
    source_name = source_name or settings.signals_source_name
    limit = limit or settings.publish_batch_size

    rows = extractions_repo.get_publishable(publish_types, limit)
    summary = PublishSummary()

    for row in rows:
        summary.considered += 1
        payload = build_signal_payload(row, source_name)
        try:
            resp = signals_client.post_signal(payload) or {}
            status = resp.get("status", "unknown")
            extractions_repo.mark_published(row.id, payload["idempotency_key"], status)
            if status == "accepted":
                summary.posted += 1
            elif status == "duplicate":
                summary.duplicate += 1
            elif status == "unresolved":
                summary.unresolved += 1
            else:
                summary.other += 1
            counter = _STATUS_COUNTER.get(status)
            if counter:
                state_repo.incr_counter(WORKER, counter)
            logger.info("published %s -> %s (%s)", row.ticker, status, row.doc_id)
        except Exception:  # noqa: BLE001 - isolated; left unpublished for retry
            summary.failed += 1
            state_repo.incr_counter(WORKER, "failed")
            logger.exception("failed to publish extraction id=%s", row.id)

    return summary
