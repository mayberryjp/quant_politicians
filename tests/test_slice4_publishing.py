"""Slice 4 tests: payload mapping and purchase-only publishing to quant_signals."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.house import PublishableExtraction
from app.redis.repository import StateRepository
from app.services.house_publish import build_signal_payload, publish_signals


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

@dataclass
class ExRow:
    id: int
    doc_id: str
    ticker: str | None
    transaction_type: str
    transaction_date: str | None = None
    amount_range: str | None = None
    owner: str | None = None
    llm_model: str | None = "test-vision"
    first: str = ""
    last: str = ""
    state_dst: str | None = None
    filing_date: str | None = None
    published: bool = False
    idempotency_key: str | None = None
    signal_result: str | None = None


class InMemoryExtractionsRepo:
    def __init__(self):
        self.rows: dict[int, ExRow] = {}

    def add(self, **kwargs) -> ExRow:
        row = ExRow(**kwargs)
        self.rows[row.id] = row
        return row

    def get_publishable(self, publish_types, limit):
        types = {t.lower() for t in publish_types}
        selected = [
            r for r in self.rows.values()
            if not r.published and r.ticker and r.transaction_type.lower() in types
        ]
        return [
            PublishableExtraction(
                id=r.id, doc_id=r.doc_id, ticker=r.ticker, transaction_type=r.transaction_type,
                transaction_date=r.transaction_date, amount_range=r.amount_range, owner=r.owner,
                llm_model=r.llm_model, first=r.first, last=r.last, state_dst=r.state_dst,
                filing_date=r.filing_date,
            )
            for r in selected[:limit]
        ]

    def mark_published(self, extraction_id, idempotency_key, signal_result):
        r = self.rows[extraction_id]
        r.published, r.idempotency_key, r.signal_result = True, idempotency_key, signal_result


class FakeSignals:
    def __init__(self, status="accepted", raises=False):
        self.status = status
        self.raises = raises
        self.posted: list[dict] = []

    def post_signal(self, payload):
        if self.raises:
            raise RuntimeError("signals down")
        self.posted.append(payload)
        return {"status": self.status}


def _seed_purchase(repo, id_=1, doc_id="100001", ticker="AAPL"):
    return repo.add(
        id=id_, doc_id=doc_id, ticker=ticker, transaction_type="purchase",
        transaction_date="04/15/2026", amount_range="$1,001 - $15,000", owner="SP",
        first="Richard", last="Aaron", state_dst="MI04", filing_date="2026-04-20",
    )


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

class TestPayload:
    def test_build_payload(self):
        row = PublishableExtraction(
            id=1, doc_id="100001", ticker="AAPL", transaction_type="purchase",
            transaction_date="04/15/2026", amount_range="$1,001 - $15,000", owner="SP",
            first="Richard", last="Aaron", state_dst="MI04", filing_date="2026-04-20",
            llm_model="test-vision",
        )
        payload = build_signal_payload(row, "house-disclosures-v1")
        assert payload["source"] == "house-disclosures-v1"
        assert payload["idempotency_key"] == "house-disclosures-v1:100001:AAPL"
        assert payload["ticker"] == "AAPL"
        assert payload["direction"] == "long"
        assert "purchase of AAPL" in payload["reason"]
        assert "purchase" in payload["tags"]
        assert payload["metadata"]["doc_id"] == "100001"


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------

class TestPublish:
    def test_happy_posts_and_marks_published(self, fake_redis):
        state = StateRepository(fake_redis)
        repo = InMemoryExtractionsRepo()
        _seed_purchase(repo, 1, "100001", "AAPL")
        _seed_purchase(repo, 2, "100002", "TSLA")
        client = FakeSignals(status="accepted")

        summary = publish_signals(
            extractions_repo=repo, state_repo=state, signals_client=client,
            publish_types=["purchase"],
        )

        assert (summary.posted, summary.failed) == (2, 0)
        assert repo.rows[1].published and repo.rows[2].published
        assert repo.rows[1].idempotency_key == "house-disclosures-v1:100001:AAPL"
        assert state.get_counters("house", ["signals_posted"])["signals_posted"] == 2

    def test_no_republish_on_second_run(self, fake_redis):
        state = StateRepository(fake_redis)
        repo = InMemoryExtractionsRepo()
        _seed_purchase(repo, 1)
        client = FakeSignals(status="accepted")

        publish_signals(extractions_repo=repo, state_repo=state, signals_client=client, publish_types=["purchase"])
        second = publish_signals(extractions_repo=repo, state_repo=state, signals_client=client, publish_types=["purchase"])

        assert second.considered == 0  # already published
        assert len(client.posted) == 1

    def test_only_purchases_published(self, fake_redis):
        state = StateRepository(fake_redis)
        repo = InMemoryExtractionsRepo()
        _seed_purchase(repo, 1, "100001", "AAPL")
        repo.add(id=2, doc_id="100002", ticker="TSLA", transaction_type="sale", first="A", last="B")
        client = FakeSignals(status="accepted")

        summary = publish_signals(
            extractions_repo=repo, state_repo=state, signals_client=client, publish_types=["purchase"],
        )
        assert summary.considered == 1
        assert repo.rows[2].published is False  # sale never published

    def test_duplicate_status_counted(self, fake_redis):
        state = StateRepository(fake_redis)
        repo = InMemoryExtractionsRepo()
        _seed_purchase(repo, 1)
        client = FakeSignals(status="duplicate")

        summary = publish_signals(extractions_repo=repo, state_repo=state, signals_client=client, publish_types=["purchase"])
        assert summary.duplicate == 1
        assert repo.rows[1].published and repo.rows[1].signal_result == "duplicate"
        assert state.get_counters("house", ["signals_duplicate"])["signals_duplicate"] == 1

    def test_unresolved_status_counted(self, fake_redis):
        state = StateRepository(fake_redis)
        repo = InMemoryExtractionsRepo()
        _seed_purchase(repo, 1)
        client = FakeSignals(status="unresolved")

        summary = publish_signals(extractions_repo=repo, state_repo=state, signals_client=client, publish_types=["purchase"])
        assert summary.unresolved == 1
        assert state.get_counters("house", ["signals_unresolved"])["signals_unresolved"] == 1

    def test_error_leaves_unpublished(self, fake_redis):
        state = StateRepository(fake_redis)
        repo = InMemoryExtractionsRepo()
        _seed_purchase(repo, 1)
        client = FakeSignals(raises=True)

        summary = publish_signals(extractions_repo=repo, state_repo=state, signals_client=client, publish_types=["purchase"])
        assert summary.failed == 1
        assert repo.rows[1].published is False  # retryable next cycle
        assert state.get_counters("house", ["failed"])["failed"] == 1
