# Data Source — U.S. House Financial Disclosures

Published by the Clerk of the House under the STOCK Act at
`https://disclosures-clerk.house.gov`.

## Annual index (the manifest)

- Per-year ZIP: `{HOUSE_FD_BASE_URL}/public_disc/financial-pdfs/{YEAR}FD.zip`
- Contains `{YEAR}FD.xml` (parsed) and `{YEAR}FD.txt`.
- The ZIP is the full year to date and is re-published as filings arrive, so each cycle re-downloads
  it and diffs new `DocID`s against `house_filings`.

### `<Member>` fields

| Field | Notes |
|---|---|
| `Prefix/Last/First/Suffix` | Filer name |
| `FilingType` | One-letter code; **PTR** is the trade-bearing report (default `TARGET_FILING_TYPES=P`) |
| `StateDst` | State + district (e.g. `MI04`) |
| `Year` | Filing year |
| `FilingDate` | `M/D/YYYY` |
| `DocID` | Stable id; used to build the document URL |

A row is **malformed** (skipped + counted) when it lacks a `DocID` or a numeric `Year`.

## Individual documents (PDFs)

- PTR: `{BASE}/public_disc/ptr-pdfs/{YEAR}/{DocID}.pdf`
- Annual/other: `{BASE}/public_disc/financial-pdfs/{YEAR}/{DocID}.pdf`

Short numeric `DocID`s are typically scanned paper; long 8-digit ids are electronic. Both are handled
identically: pages are rendered to images and passed to a vision LLM for OCR + extraction.

## Pipeline status transitions (`house_filings.status`)

`new` → `fetched` → `extracted` → (extractions `published`); `failed` on error.
Only `FilingType` in `TARGET_FILING_TYPES` is fetched; the full index is stored regardless.
