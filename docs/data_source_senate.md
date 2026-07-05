# Data Source — U.S. Senate Financial Disclosures (eFD)

Published via the Senate **Electronic Financial Disclosure (eFD)** search app at
`https://efdsearch.senate.gov`. Unlike the House, there is **no bulk XML/ZIP index and no official
API** — reports are discovered through the site's search endpoint, which requires a one-time
agreement handshake per session.

> **Compliance:** the Ethics in Government Act §105(c) prohibits commercial use / solicitation based
> on this data. This pipeline is an operator opt-in, non-commercial watchlist feed only.

## Session & agreement handshake

1. `GET /search/home/` → parse the `csrfmiddlewaretoken` from the page.
2. `POST /search/home/` with `prohibition_agreement=1` (+ CSRF) → sets the session cookie.
3. Subsequent `POST /search/report/data/` calls reuse the cookie. A 403 / redirect back to the
   agreement page means the session expired → re-authorize once and retry.

The session (cookies + CSRF) is cached in Redis at `qp:senate:session` with a TTL so cycles reuse it.

## Search endpoint (the manifest)

`POST {SENATE_EFD_BASE_URL}/search/report/data/` returns DataTables JSON. Key request fields:

| Field | Notes |
|---|---|
| `report_types[]` | Numeric report-type code(s); **PTR** is trade-bearing (`SENATE_REPORT_TYPES`) |
| `filer_types[]` | `SENATE_FILER_TYPES=all` covers every filer |
| `submitted_start_date` / `submitted_end_date` | `MM/DD/YYYY` window |
| `draw` / `start` / `length` | Pagination (`SENATE_SEARCH_PAGE_SIZE`) |

Each result row yields a filer name, filed date, and a report link.

## Report identifier & types

- The **report UUID** is embedded in the link: `/search/view/ptr/<uuid>/` (electronic) or
  `/search/view/paper/<uuid>/` (scanned paper).
- **Electronic (HTML):** has a structured transactions table **including a Ticker column**, used as a
  deterministic cross-check against the LLM output (mismatches flagged `needs_review`).
- **Paper (PDF):** scanned; rendered to images and passed to the vision LLM (OCR + extraction).

Coverage is 2012–present.

## Since-last-run & backfill

- Watermark `qp:senate:poll:watermark` holds the newest processed `filed_date`; the next window starts
  there. New reports are diffed by `report_uuid` against `senate_filings`.
- Backfill: set `SENATE_BACKFILL_START_DATE` (e.g. `01/01/2012`); processed once, then recorded in
  `qp:senate:backfill:done`.

## Politeness / rate limiting

The eFD site throttles. Enforce `SENATE_REQUEST_DELAY`, exponential backoff on 429/5xx, an identifying
`User-Agent`, and on-disk caching of fetched reports (`{DOC_CACHE_DIR}/senate/{sha[:2]}/{sha}.{ext}`).

## Pipeline status transitions (`senate_filings.status`)

`new` → `fetched` → `extracted` → (extractions `published`); `failed` on error.
