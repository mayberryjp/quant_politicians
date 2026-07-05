# quant_politicians

Ingest U.S. congressional financial disclosures, extract the securities named in each
filing with a local **Ollama** LLM, and publish each **purchase** as a signal to the
[`quant_signals`](https://github.com/mayberryjp/quant_signals) watchlist.

Two long-lived workers run under **supervisord** and wake on a schedule to process
filings **since the last run**:

- **House** (`app/services/house_worker.py`) - annual ZIP/XML index -> filing PDFs.
  Spec: issue #1.
- **Senate** (`app/services/senate_worker.py`) - eFD search endpoint -> HTML/PDF reports.
  Spec: issue #2.

Both share one core: vision-LLM OCR + structured extraction, ticker validation, and the
`quant_signals` producer contract.

## Quick Start

```bash
pip install -e ".[dev]"

# Run API server
python3 -m app.main

# Run the House worker (single pass)
python3 -m app.services.house_worker --once

# Run the House worker on a schedule
python3 -m app.services.house_worker --schedule 3600

# Run the Senate worker (single pass / scheduled)
python3 -m app.services.senate_worker --once
python3 -m app.services.senate_worker --schedule 3600

# Run tests
pytest -v
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/politicians-cache/health` | Liveness (no Redis dependency) |
| GET | `/politicians-cache/ready` | Readiness (Redis + worker heartbeats) |
| GET | `/politicians-cache/stats` | Operational counters + last run |

## Architecture

Python 3.11+, `bottle` served by `waitress`, Redis (`qp:` key prefix) for run-state and
coordination, PostgreSQL + SQLAlchemy + Alembic for durable filing/extraction state, and
`pydantic-settings` for configuration. Follows the conventions established in `quant_signals`.

Configuration is environment-driven - see [.env.example](.env.example).

## Development status

Delivered in slices, one issue at a time:

- **Slice 0 (this PR)** - shared scaffold: config, Redis client/keys/repository, health/
  readiness/stats API, House worker skeleton, supervisord/Docker/compose, Alembic scaffold, CI.
- Slice 1+ - House index ingestion, document retrieval, vision-LLM extraction, signal publishing
  (see issue #1).

## Specs

- [House ingestion spec](specs/house-financial-disclosure-ingestion.md) (issue #1)
- [Senate ingestion spec](specs/senate-financial-disclosure-ingestion.md) (issue #2)