# Operational Runbook

## Processes

`supervisord` runs three programs (see `supervisord.conf`):

- `alembic-migrate` — one-shot `alembic upgrade head`
- `politicians-api` — `python3 -m app.main` (bottle + waitress)
- `house-worker` — `python3 -m app.services.house_worker --schedule $POLL_INTERVAL`

## Health / readiness / stats

```bash
curl http://localhost:8017/politicians-cache/health
# {"status": "ok"}

curl http://localhost:8017/politicians-cache/ready
# {"status": "ready|degraded|not_ready", "redis": "ok", "heartbeats": {"house": "..."}}

curl http://localhost:8017/politicians-cache/stats
# {"house": {"filings_seen": N, "new_filings": N, "docs_fetched": N, ...}}
```

- `status: degraded` → Redis is up but the worker heartbeat is missing/stale (worker down or mid-start).
- `status: not_ready` → Redis unreachable; ingestion/coordination will fail.

## Read API

```bash
# Filings (filters: status, filing_type, year; pagination: page, page_size)
curl "http://localhost:8017/house/filings?status=fetched&page=1&page_size=25"
curl "http://localhost:8017/house/filings/10078673"

# Extractions (filters: doc_id, ticker, published)
curl "http://localhost:8017/house/extractions?ticker=AAPL&published=true"
```

## Run the worker manually

```bash
python3 -m app.services.house_worker --once      # single pass
python3 -m app.services.house_worker --schedule 3600
```

## Redis inspection

```bash
redis-cli KEYS "qp:*"
redis-cli GET "qp:house:poll:heartbeat"
redis-cli GET "qp:house:poll:last_run"
redis-cli GET "qp:house:poll:watermark"
redis-cli GET "qp:house:counter:signals_posted"
redis-cli SMEMBERS "qp:house:backfill:done"
redis-cli GET "qp:house:lock:poll"          # single-flight cycle lock
```

## Backfill

Set `BACKFILL_YEARS` (e.g. `2023,2024`) or `HOUSE_FD_YEARS=all`. A backfill year is processed once,
then recorded in `qp:house:backfill:done` and skipped on later cycles.

## Troubleshooting

- **No new filings** — the yearly ZIP is re-diffed by `DocID`; a steady state yields `new_filings=0`.
- **Filings stuck in `failed`** — inspect `last_error` via `/house/filings?status=failed`; network or
  render/extract issues. Fix and (for now) re-queue by resetting status to `new`/`fetched` in Postgres.
- **`extraction skipped`** — `OLLAMA_MODEL` is unset (required, no default).
- **`publishing skipped`** — `SIGNALS_API_URL` is unset.
- **Documents not downloading** — verify the House site is reachable and `TARGET_FILING_TYPES` matches
  the live PTR code.
