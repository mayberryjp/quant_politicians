# Operational Runbook

## Processes

`supervisord` runs four programs (see `supervisord.conf`):

- `alembic-migrate` — one-shot `alembic upgrade head`
- `politicians-api` — `python3 -m app.main` (bottle + waitress)
- `house-worker` — `python3 -m app.services.house_worker --schedule $POLL_INTERVAL`
- `senate-worker` — `python3 -m app.services.senate_worker --schedule $POLL_INTERVAL`

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

# Senate filings (filters: status, report_type, is_paper; pagination: page, page_size)
curl "http://localhost:8017/senate/filings?status=fetched&page=1&page_size=25"
curl "http://localhost:8017/senate/filings/<report_uuid>"

# Senate extractions (filters: report_uuid, ticker, published)
curl "http://localhost:8017/senate/extractions?ticker=AAPL&published=true"
```

## Run the worker manually

```bash
python3 -m app.services.house_worker --once      # single pass
python3 -m app.services.house_worker --schedule 3600
python3 -m app.services.senate_worker --once     # single pass
python3 -m app.services.senate_worker --schedule 3600
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
redis-cli GET "qp:senate:poll:heartbeat"
redis-cli GET "qp:senate:poll:watermark"
redis-cli GET "qp:senate:session"           # cached eFD session (cookies + CSRF)
redis-cli GET "qp:senate:lock:poll"
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

## Senate specifics

- **Processes** — `senate-worker` runs alongside `house-worker`; `/ready` is `ready` only when **both**
  worker heartbeats are present.
- **Session / agreement errors** — a 403 or redirect to the agreement page means the eFD session
  expired; the worker re-authorizes once and retries. Persistent failures: clear `qp:senate:session`
  and confirm the agreement `POST` still uses `prohibition_agreement=1`.
- **Throttling (429/5xx)** — the eFD site rate-limits; raise `SENATE_REQUEST_DELAY` and rely on the
  exponential backoff. Fetched reports are cached on disk, so retries do not re-download.
- **No new reports** — reports are diffed by `report_uuid`; a steady state yields `new_reports=0`. Check
  `qp:senate:poll:watermark` and that `SENATE_REPORT_TYPES` matches the live PTR code.
- **Ticker mismatches** — electronic reports cross-check the HTML Ticker column against the LLM; the
  `ticker_mismatches` counter and `needs_review` flag surface hallucinated/uncertain symbols.
- **Backfill** — set `SENATE_BACKFILL_START_DATE`; processed once then recorded in `qp:senate:backfill:done`.
