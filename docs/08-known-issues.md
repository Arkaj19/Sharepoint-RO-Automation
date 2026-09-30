# 08 — Known Issues, Risks and Recommendations

> Part of the [documentation set](README.md). Each item states **how it was
> established**:
> **Reproduced** = I ran the code and saw it; **Observed** = seen in the committed
> data or live SharePoint listing; **Code-read** = follows from reading the source
> (not executed). Nothing in the codebase was modified to produce this document;
> fixes applied afterwards are marked **Fixed** / **Addressed** in the summary table (the
> sections below still describe the original problem). The agentic restructure is described
> in [09](09-agentic-architecture.md).

## Summary

| # | Issue | Severity | Evidence |
|---|---|---|---|
| [1](#1-openpyxl-was-missing-from-requirementstxt-fixed) | ~~`openpyxl` missing from `requirements.txt`~~ | **Fixed** — `openpyxl==3.1.5` added | Code-read |
| [2](#2-validation-cannot-match-the-committed-data-header-and-value-mismatch) | Validation can't match the committed data (headers *and* key values) | **Addressed** — YAML names both columns; Agent 1 proposes aliases, key normalisation and crosswalk (see [09](09-agentic-architecture.md)) | Reproduced |
| [3](#3-rule-6-crashes-on-duplicate-keys) | Rule 6 crashes (HTTP 500) when either file has duplicate keys | **Fixed** — unique keys only; rules isolated | Reproduced |
| [4](#4-a-fetch-that-matches-no-files-overwrites-the-good-csv-with-an-empty-one) | A fetch matching no files overwrites the good CSV with an empty one; later preview 500s | **Fixed** — dataset reported as error, current version kept | Reproduced (pandas behaviour) |
| [5](#5-fetch-does-not-preserve-text-formatting-dtypestr) | Fetch doesn't preserve text formatting (`dtype=str`) | **Fixed** — all reads are text-only | Observed + Code-read |
| [6](#6-condition_notes-are-loaded-but-unused-and-row-counts-are-compared-strictly) | `condition_notes` unused; strict row-count equality | **Partly addressed** — structured filters + fan-out in the YAML; the unexplained row gap remains open | Code-read |
| [7](#7-no-authentication-and-verbose-errors) | No authentication; verbose errors; wide-open data endpoints | Medium (High if exposed) | Code-read |
| [8](#8-robustness-of-the-sharepoint-client) | No timeouts; auth failures reported as 502; `.xls` unsupported | **Mostly fixed** — timeouts, 429/503 retry, `.xls` no longer matched | Code-read |
| [9](#9-rule-implementation-gaps) | Rule gaps (false positives on pandas 2.2, unchecked precision, ECC dupes) | **Mostly fixed** — blank handling on text reads, `number`/`date` compares, ECC duplicate keys reported | Code-read |
| [10](#10-input-validation-on-family-and-source_type) | `family`/`source_type` not allow-listed on download | **Fixed** — object and side are checked | Code-read |
| [11](#11-performance-and-scaling) | Whole-file reads/in-memory workbooks | Low | Code-read |
| [12](#12-repository-hygiene-and-stale-content) | Committed data & zips, stale README, dead code, UI inconsistencies | **Mostly fixed** — zips and dead code removed, README rewritten, `backend/data/` ignored (the tracked CSVs still need `git rm --cached`) | Observed |
| [13](#13-no-tests-no-ci-no-lint) | No tests, CI or lint | **Partly fixed** — 35 pytest tests (`backend/tests`); no CI or lint yet | Observed |

---

## 1. `openpyxl` was missing from `requirements.txt` (fixed)

> **Fixed:** `openpyxl==3.1.5` is now pinned in `backend/requirements.txt` (uncommitted
> at the time of writing). 3.1.5 meets pandas 2.2.2's minimum (3.1.0) and supports
> Python 3.11. Existing environments need `pip install -r requirements.txt` again.
> The description below records the original problem.

`merge_service.py` calls `pd.read_excel(..., engine="openpyxl")` (ECC fetch) and
`df.to_excel(..., engine="openpyxl")` (download); `validate.py` reads uploaded
Excel files; `mapping_loader --rebuild` imports `openpyxl` directly. `requirements.txt`
did not list it, and pandas does not install it as a dependency.

**Impact:** a fresh environment installed from `requirements.txt` gets HTTP 502
("Missing optional dependency 'openpyxl'") on `POST /api/fetch/ecc`, HTTP 500 on
`GET /api/download/...`, and HTTP 400 on Excel uploads. (The original developer's
machine evidently had it, since the ECC CSVs exist.)

**Fix (applied):** a pinned `openpyxl==3.1.5` line in `backend/requirements.txt`.

---

## 2. Validation cannot match the committed data (header *and* value mismatch)

**Reproduced.** Running the real `validation_service.validate()` on the four committed
CSVs gives:

| Sheet | Field coverage | Mandatory | Type/length | Record count | Key integrity | Value transformation |
|---|---|---|---|---|---|---|
| MARC | ❌ 146 of 146 missing | ❌ column missing | ✅ *(vacuous)* | ❌ 29 387 vs 24 577 (−4 810) | ⚠ skipped | ⚠ skipped |
| MBEW | ❌ 41 of 41 missing | ❌ column missing | ✅ *(vacuous)* | ❌ 15 746 vs 22 615 (+6 869) | ⚠ skipped | ⚠ skipped |

**Root cause 1 — headers are inverted.** The engine expects ECC = technical names
(matched to the mapping's `source_field`) and S/4 = friendly labels (matched to the
mapping's `s4_field`). The data is the reverse: **S4 files use technical names —
146/146 (MARC) and 41/41 (MBEW) match `source_field` exactly** — and ECC files use SAP
screen labels (`Material`, `Plant`, …), of which only 48 / 24 happen to equal a friendly
label. Two consequences: Rule 1 reports everything missing, and the "pass" on Rule 3 is
**vacuous** (no column resolved, so nothing was examined). Don't read a green Rule 3 as
evidence.

**Root cause 2 — even with headers fixed, key values don't match.** (Measured; see
[07 §4.2](07-data-and-mapping.md#42-key-values-differ-between-systems).)

* Material numbers: ECC un-padded (`100014`) vs S4 18-char zero-padded
  (`000000009687900109`). Raw overlap 614 (MARC) / 523 (MBEW); after stripping leading
  zeros 10 695 / 9 939.
* Plant / valuation-area codes are re-coded (ECC `1021…1055`, S4 `US27…US33`, `CA02`) — a
  crosswalk is required and none exists in the repo.

**Root cause 3 — value formatting.** Rule 6 compares trimmed strings exactly; S4 has
`1.0`/`0.0` style numerics.

**Options** (a team decision — I have not changed code):

1. **Normalise at load time.** Add a column-alias layer: rename ECC headers to
   the mapping's technical names via a label→technical crosswalk (for SAP tables this is
   typically the `DD03T` field-text table; it is *not* in the repo), and rename S4
   technical headers to `s4_field` labels using the mapping (this half is already
   derivable from the workbook — 146/146 and 41/41 match).
2. **Normalise values in the key builder**: `lstrip("0")` (or zero-pad to 18) on
   `Product Number`; add a plant-code translation table for `Plant`/`Valuation Area`.
3. **Make comparisons type-aware** in Rule 6 (numeric compare for `Number` fields, date
   parsing for `Date`), or read/write everything as `dtype=str` consistently.
4. Alternatively have the source extracts produced with the header convention the
   engine expects.
5. Add a unit test with a tiny pair of correctly-shaped frames so this contract can't
   silently drift again (the docstring's contract is not enforced anywhere).

Also update `validation_service`'s module docstring, which currently states the
contract the data does not follow.

---

## 3. Rule 6 crashes on duplicate keys

**Reproduced.** With one duplicate key on either side, `rule_value_transformation` runs
`ecc_indexed.loc[common_keys, col]` on a non-unique index. That returns *more* rows
than `common_keys` (the de-duplication above it only trims `common_keys`), so the
comparison `ecc_vals.values != s4_vals.values` compares arrays of different lengths:

```
ValueError: operands could not be broadcast together with shapes (3,) (2,)
```

The exception is not caught in the route, so `/api/validate/*` returns **HTTP 500**
with no report — even though Rule 5 would already have *reported* those duplicates.
The committed S4 MARC file has **111 duplicate `PRODUCT + werks` keys**, so this will
trigger as soon as issue #2 is fixed.

**Fix:** de-duplicate (`keep="first"`, or aggregate) both indexed frames before the
`.loc`, or skip duplicates explicitly and report them; wrap rules in try/except so a
single rule failure yields a `warning` result instead of a 500.

---

## 4. A fetch that matches no files overwrites the good CSV with an empty one

`fetch_*_files` writes the combined CSV **unconditionally**, using an empty
`DataFrame` if no filename matched. Pandas serialises that as a bare newline and
`pd.read_csv` on it raises `EmptyDataError: No columns to parse from file` **(both
reproduced)**. So a renamed SharePoint file, a wrong `SP_FOLDER_PATH`, or a case
mismatch (prefix matching is case-sensitive) will:

1. return HTTP 200 with `source_files: 0, row_count: 0`;
2. destroy the previous good combined file; and
3. make preview return an unhandled 500 (`EmptyDataError` isn't caught) and validation
   fail.

Fetching is also **non-atomic** (writes happen family by family) and there is no
history — a bad fetch cannot be rolled back except via git (the CSVs happen to be
committed).

**Fix:** skip writing (and return 4xx/5xx or a clear warning) when `matching` is empty;
write to a temp file then `os.replace`; catch `EmptyDataError` in `get_preview`.

---

## 5. Fetch does not preserve text formatting (`dtype=str`)

`fetch_s4_files` reads each CSV with `pd.read_csv(..., low_memory=False)` and
`fetch_ecc_files` reads Excel with no `dtype`. Pandas infers numeric types, so:

* An all-numeric identifier column loses **leading zeros** (the committed S4 `PRODUCT`
  column kept its 18-digit padding, but that appears to be luck — the column also holds
  non-numeric material numbers, which forces string type).
* Integer columns containing blanks become floats, which is why the S4 CSVs contain
  `1.0`, `0.0`, `40.0` **(observed)** and why preview shows floats.
* Concatenating parts that inferred *different* dtypes can produce mixed columns.

**Fix:** read with `dtype=str, keep_default_na=False` (or `dtype=str` and treat empty as
NaN) in both fetchers, and in preview/download, so what's stored is what the source held.

---

## 6. `condition_notes` are loaded but unused, and row counts are compared strictly

The mapping records the SQL filters that shape the S/4 extract (plant `1021`,
`LVORM IS NULL`, `MTART <> 'UNBW'`, `DERIVED_WERKS <> '1021'`). `get_condition_notes()`
exists but is never called, and Rule 4 demands exact row equality. Any legitimately
filtered extract therefore "fails" Rule 4, and the UI gives no hint why. Even
restricting ECC to the five plants with S4 files does **not** reconcile the counts
([07 §4.3](07-data-and-mapping.md#43-row-counts-do-not-simply-reconcile-by-plant)), so
the correct expected relationship needs to be defined by the migration owners.

**Fix (design):** surface the condition text in the report, or encode the filter so
Rule 4 compares against the *expected* subset.

---

## 7. No authentication and verbose errors

* No auth of any kind on any endpoint. Anyone reaching the port can trigger fetches,
  preview and download the extracts (which are business master data), and upload
  files for parsing.
* `CORSMiddleware` allows exactly one origin, but with `allow_credentials=True`,
  `allow_methods=["*"]`, `allow_headers=["*"]`. CORS does not protect against
  non-browser clients.
* Error handlers return `str(exc)` — Graph URLs, MSAL messages and file paths
  reach the browser. Unhandled 500s return a generic message.
* Uploads have no size limit or content-type check.
* Combined CSVs and two zip snapshots containing real data are committed to git
  (see #12).

**Fix:** put the API behind SSO/API-gateway auth (or add FastAPI auth), restrict network
exposure, cap upload size, and log details server-side while returning generic messages.

---

## 8. Robustness of the SharePoint client

* `requests.get` is called with **no timeout** (site lookup, listing, every download).
* `SharePointAuthError` is defined and raised but the route imports it and never catches
  it, so bad credentials produce 502 instead of the intended 401 (the older, commented
  version of the route did map it to 401).
* Downloads load each entire file into memory; ECC workbooks are 6–26 MB on the wire.
* `.xls` matches the ECC filename filter but the reader is forced to `openpyxl`, which
  cannot open `.xls`; only `.xlsx`/`.XLSX` work.
* Graph throttling (HTTP 429) isn't handled — there are no retries.
* The token isn't refreshed; fine per-request, wrong if the client is ever reused.

---

## 9. Rule implementation gaps

*(Code-read unless noted.)*

* **Rule 3 false positives on pandas 2.2.x:** `series.astype(str)` yields `"nan"` for
  missing values, so any blank cell in a Text field with mapped length < 3 (20 MARC and
  4 MBEW indicator fields, each length 1) would count as "exceeds max length 1". Not
  reproduced here (scratch env has pandas 3.0), but this is standard pandas 2.x behaviour
  and the project pins 2.2.2.
* **Number `length` and `decimal` are never enforced**, despite the module describing
  "type & length conformance".
* **Duplicate keys are checked only on the S/4 side** (Rule 5).
* Rule 5's samples are taken from unordered set differences, so they differ run to run.
* `details` are capped at 20 (Rule 5: 5 samples per issue), but summaries carry the full
  counts — good — the UI doesn't say details are truncated.
* Rule 2's summary says "have blank values" even when the cause is a *missing column*.
* `_violates_type` accepts any string parseable by `float()` — `"nan"`, `"inf"`,
  `"1e5"` — as numeric.
* Rule 6 name suggests transformation logic; it only checks equality (see
  [05](05-validation-engine.md#rule-6--field-level-value-transformation-accuracy-value_transformation)).

---

## 10. Input validation on `family` and `source_type`

`get_preview` allow-lists both; `build_xlsx_bytes` does not — it only builds a path from
them and checks it exists: `OUTPUT_DIR/{family}/{source_type}/{family}_{source_type}_combined.csv`.
FastAPI path segments can't contain `/`, but on Windows a percent-encoded backslash
could still influence the path. The fixed `_combined.csv` suffix limits what can be read,
so severity is low, but the two functions should share one allow-list.

---

## 11. Performance and scaling

* Preview reads the **entire** CSV to return 20 rows (to compute `total_rows`).
* Download builds the entire workbook in RAM (MARC ECC: 29 k × 249) and the browser
  buffers it as a Blob.
* Fetch is fully sequential and synchronous; large ECC workbooks (25 MB) are parsed by
  `openpyxl`, which is slow. A request can outlive proxy timeouts.
* Key construction uses a row-wise Python `apply` (≈ 0.33 s per 29 k rows, measured).
* No caching of parsed files between requests.

---

## 12. Repository hygiene and stale content

| Item | Detail |
|---|---|
| Committed data | `.gitignore` has `backend/data/combined/*.csv`, which does **not** match the nested `MARC/ECC/…` paths, so ~28 MB of CSVs are tracked. Use `backend/data/combined/**/*.csv`. Consider whether real extracts should be in git at all. |
| Zip snapshots | `backend/backend_Zip.zip` (1.9 MB, contains `__pycache__` and the CSVs) and `frontend/frontend_zip.zip` are older copies of the source. Delete or ignore. |
| Root `README.md` is stale | Describes `POST /api/fetch` and `GET /api/preview/{name}` (now `/api/fetch/s4|ecc` and `/api/preview/{family}/{source_type}`), omits validation/ECC/download, references a `logo.png` (code uses `gyansys-logo.png`), and describes adding a family as one edit (there are now two dicts, plus validation prerequisites). |
| `.env.example` | Missing `SP_ECC_FOLDER_PATH`. |
| Dead code | Large commented-out legacy blocks in `routes/fetch.py`, `services/merge_service.py`, `services/sharepoint_service.py`, `api/fetchApi.js`, `DataFetchPage.jsx`, `FetchActionCard.jsx`, `SummaryCard.jsx`. Unused: `ErrorResponse` schema, `get_condition_notes`, `SharePointAuthError` import in the route. |
| Duplication | `validate.py::_read_upload` duplicates `table_io.read_table`; `fetchApi.js` repeats error handling that `validateApi.js` shares. |
| Naming/versions | API title "GyanSys Migration Tool API" v0.1.0; header "Sharepoint Automation Tool"; footer "Version 1.0.0"; `package.json` 0.1.0; repo "Sharepoint-RO-Automation". |
| UI | Header "Connected" indicator is hard-coded true; `SummaryCard` swallows download errors; `StatusPanel` idle text references a button label that no longer exists; 422 errors render as `[object Object]`; no 404 route; `FetchActionCard` label parses `title.split(" ")[1]`; `RuleDetailValue` recursion is unbounded. |
| Config | `Settings` reads env at import time with no validation (empty secrets fail late); relative `SP_OUTPUT_DIR` depends on the working directory. |
| Concurrency | Parallel fetches race on the same files; nothing is locked. |
| "Latest" | `find_latest` picks by `mtime`, but since fetch always overwrites one fixed filename, there is only ever one candidate — the "latest files" feature has no history behind it. |

---

## 13. No tests, no CI, no lint

Neither project has tests, a test runner, CI configuration, a linter or a type checker.
The validation service and mapping loader are pure functions of DataFrames and JSON
and would be the cheapest, highest-value place to start (unit-test each rule with
tiny frames; a golden test for `_rebuild_from_xlsx` against the committed JSON).

---

## Suggested order of work

1. ~~Add `openpyxl` to requirements (#1)~~ — done.
2. Decide the header/key contract with the migration owners and fix the loader or the
   extracts (#2) — this unblocks the Validation tab entirely.
3. Make Rule 6 duplicate-safe and isolate rule failures (#3).
4. Guard against empty fetches and use `dtype=str` (#4, #5).
5. Add auth and sanitise errors before exposing beyond a trusted network (#7).
6. Tidy the repo: fix `.gitignore`, delete the zips, refresh the README (#12), add tests (#13).

## End-to-end test session (2026-09-30)

A scripted two-pass test covered 21 scenarios, each run as twin UI + API runs on local-source sandboxes. It found 25 incidents. The full reports are kept outside the repo (`RO-Automation-TestRun/`: SUMMARY.md, INCIDENTS.md, FIXES.md).

**Fixed:**
- Windows file-sharing crash of background jobs (atomic JSON writes).
- ECC changes not linked to imported fields; ECC renames not followed (now fixed via the dictionary).
- Reference-table changes producing empty "baseline" change sets.
- Forced refresh storing duplicate versions.
- Vanished supporting tables not reported.
- Re-import re-proposing and re-stamping every rule, and failing on existing filters.
- Per-plant SQL rule changes not flagged against the workbook.
- Contradicting ignore/alias ops.
- Provenance rewritten by alias/length changes.
- Validation vs shadow-compare count mismatch (one normalisation).
- Several UI feedback issues: apply progress, stage chips, reasons, percentages, persisted validation, op-card targets.

**Still open, by design or documented:**
- An ECC plant with no routing branch silently drops its rows (there is no "routing gap" warning yet).
- Value edits never produce mapping proposals.
- Workbook conflicts appear on re-import only (a refresh now announces the workbook change).
- With `LLM_PROVIDER=none`, a consistent re-coding of an already-mapped field only raises a low-agreement warning.
