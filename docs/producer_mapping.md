# Producer Mapping — quant_signals

This service is a **signal producer** for
[`quant_signals`](https://github.com/mayberryjp/quant_signals). Only **purchases** are published.

## Endpoint

`POST {SIGNALS_API_URL}/signals`

## Field mapping

| Signal field | Value |
|---|---|
| `source` | `SIGNALS_SOURCE_NAME` (default `house-disclosures-v1`) |
| `idempotency_key` | `{source}:{doc_id}:{TICKER}` (deterministic per filing + ticker) |
| `ticker` | Extracted symbol, uppercased/validated |
| `reason` | `Rep. {First} {Last} ({StateDst}) disclosed a purchase of {TICKER} ({amount}) on {date}. House PTR DocID {doc_id}, filed {filing_date}.` |
| `market` / `locale` | `SIGNAL_MARKET` / `SIGNAL_LOCALE` (`stocks` / `us`) |
| `signal_type` | `SIGNAL_TYPE` (`watchlist_candidate`) |
| `direction` | `long` (purchases only) |
| `tags` | `["congress","house","ptr","purchase","<state>"]` |
| `metadata` | `{doc_id, filing_date, member{...}, transaction{...}, llm_model, schema_version}` |

## Response handling

| Response `status` | Effect |
|---|---|
| `accepted` | Extraction marked `published`; counter `signals_posted` |
| `duplicate` | Marked `published`; counter `signals_duplicate` |
| `unresolved` | Marked `published`; counter `signals_unresolved` (unknown ticker at the watchlist) |
| transport error | Left unpublished (retried next cycle); counter `failed` |

## Dedup

- Permanent: the `published` flag on `house_extractions` (never re-posted).
- Secondary: `quant_signals` 24h idempotency window on `source` + `idempotency_key`.

Ticker authority is `quant_signals` symbol resolution — **no local symbol master**. Invalid/unparseable
tickers are stored with `ticker=NULL` and `needs_review=true`, and are never published.
