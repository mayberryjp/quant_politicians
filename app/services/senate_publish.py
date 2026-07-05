"""Publish extracted Senate purchases as signals to quant_signals (Senate Slice 5).

Mirrors the House publisher: only ``purchase`` transactions with a valid ticker are
published (``direction=long``); sales/exchanges are stored for audit but never posted.
Each extraction maps to one signal with a deterministic idempotency key
(``{source}:{report_uuid}:{TICKER}``). The ``published`` flag gives permanent dedup,
and the watchlist service's 24h idempotency window is a second guard. Response states
(``accepted`` / ``duplicate`` / ``unresolved``) are recorded and counted. Reuses the
shared ``SignalsClient`` and the House ``PublishSummary`` container.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.models.senate import SenatePublishableExtraction
from app.services.house_publish import PublishSummary

WORKER = "senate"
logger = logging.getLogger("quant_politicians.senate.publish")

_STATUS_COUNTER = {
    "accepted": "signals_posted",
    "duplicate": "signals_duplicate",
    "unresolved": "signals_unresolved",
}


def build_signal_payload(row: SenatePublishableExtraction, source: str) -> dict:
    who = f"Sen. {row.first} {row.last}".strip()
    state = f" ({row.state})" if row.state else ""
    when = row.transaction_date or "an unknown date"
    amount = f" ({row.amount_range})" if row.amount_range else ""
    reason = (
        f"{who}{state} disclosed a purchase of {row.ticker}{amount} on {when}. "
        f"Senate PTR {row.report_uuid}, filed {row.filed_date}."
    )
    tags = ["congress", "senate", "ptr", "purchase"]
    if row.state:
        tags.append(row.state[:2].lower())
    return {
        "source": source,
        "idempotency_key": f"{source}:{row.report_uuid}:{row.ticker}",
        "ticker": row.ticker,
        "reason": reason[:2000],
        "market": settings.signal_market,
        "locale": settings.signal_locale,
        "signal_type": settings.signal_type,
        "direction": "long",
        "tags": tags,
        "metadata": {
            "report_uuid": row.report_uuid,
            "filed_date": row.filed_date,
            "is_paper": row.is_paper,
            "member": {
                "first": row.first,
                "last": row.last,
                "state": row.state,
                "filer_type": row.filer_type,
            },
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
    source_name = source_name or settings.senate_signals_source_name
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
            logger.info("published %s -> %s (%s)", row.ticker, status, row.report_uuid)
        except Exception:  # noqa: BLE001 - isolated; left unpublished for retry
            summary.failed += 1
            state_repo.incr_counter(WORKER, "failed")
            logger.exception("failed to publish extraction id=%s", row.id)

    return summary
