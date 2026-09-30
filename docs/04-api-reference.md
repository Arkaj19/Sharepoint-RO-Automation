# 04 — HTTP API Reference

> **Historical (pre-agentic).** This page describes the codebase at commit `731e6cd`. The fetch/merge services, the JSON mapping and several routes described here have been replaced - see [09 — Agentic architecture](09-agentic-architecture.md) for the current structure.

> Part of the [documentation set](README.md).
> Base URL (dev): `http://localhost:8000`. All routes live under `/api`.
> Responses below marked **(observed)** were produced by running the real app
> with FastAPI's `TestClient` against the committed data; fetch endpoints
> could not be run (they need live SharePoint credentials), so their success
> payloads are derived from the code.

FastAPI also serves generated docs at `/docs` (Swagger UI), `/redoc` and
`/openapi.json`.

## Endpoint summary

| Method | Path | Purpose | Success | Typical errors |
|---|---|---|---|---|
| GET | `/api/health` | Liveness | 200 | — |
| POST | `/api/fetch/s4` | Pull S4 (Databricks) CSVs from SharePoint, merge, save | 200 `FetchResponse` | 502 |
| POST | `/api/fetch/ecc` | Pull ECC (DAP) Excel files from SharePoint, merge, save | 200 `FetchResponse` | 502 |
| GET | `/api/preview/{family}/{source_type}` | First *N* rows of a stored combined CSV | 200 `PreviewResponse` | 404, 422, 500 |
| GET | `/api/download/{family}/{source_type}` | Stored CSV converted to `.xlsx` on the fly | 200 binary | 404, 500 |
| GET | `/api/validate/latest` | Validate the stored ECC + S4 combined files for one sheet | 200 `ValidationReportResponse` | 404, 422, 500 |
| POST | `/api/validate/upload` | Validate two uploaded files | 200 `ValidationReportResponse` | 400, 422, 500 |

Path parameter domains:

| Parameter | Allowed values | Enforcement |
|---|---|---|
| `family` | `MARC`, `MBEW` | Checked in `get_preview` (404 otherwise). **Not** checked in `build_xlsx_bytes` (only file existence). |
| `source_type` | `S4`, `ECC` (case-sensitive) | Same as above. |
| `sheet` (query) | `MARC`, `MBEW` | FastAPI `Literal` → 422 otherwise. |

There is no authentication, rate limiting, pagination (other than `limit` on
preview) or versioning.

---

## `GET /api/health`

```bash
curl http://localhost:8000/api/health
```
```json
{"status": "ok"}
```
Does not verify SharePoint or disk access.

---

## `POST /api/fetch/s4`

Pulls every file in the SharePoint folder `SP_FOLDER_PATH` (default
`Databricks Files`) whose name starts with `S_MARC#FreeText` or
`S_MBEW#FreeText` and ends with `.csv`; concatenates each family; writes
`{OUTPUT_DIR}/MARC/S4/MARC_S4_combined.csv` and
`{OUTPUT_DIR}/MBEW/S4/MBEW_S4_combined.csv`.

No request body. **Blocking** — the response arrives only after all downloads
finish (no timeout on the outbound Graph calls).

**200 response** (`FetchResponse`; values illustrative, structure from the code):

```json
{
  "total_files_fetched": 6,
  "combined_files": [
    {
      "name": "MARC_S4",
      "family": "MARC",
      "source_type": "S4",
      "source_files": 3,
      "row_count": 24577,
      "saved_path": "E:\\projects\\Sharepoint-RO-Automation\\backend\\data\\combined\\MARC\\S4\\MARC_S4_combined.csv"
    },
    {
      "name": "MBEW_S4",
      "family": "MBEW",
      "source_type": "S4",
      "source_files": 3,
      "row_count": 22615,
      "saved_path": "…\\MBEW\\S4\\MBEW_S4_combined.csv"
    }
  ],
  "message": "S4 Databricks files fetched and merged."
}
```

| Field | Meaning |
|---|---|
| `total_files_fetched` | Total number of SharePoint files downloaded across both families. |
| `combined_files[].name` | `"{family}_{source_type}"`. |
| `combined_files[].source_files` | How many SharePoint files fed this family. **0 is a success response** and still overwrites the CSV with an empty one. |
| `combined_files[].row_count` | Rows in the combined DataFrame. |
| `combined_files[].saved_path` | Absolute path on the **server**. |

**Errors:** any exception (bad credentials, Graph 403/404, network error, pandas
parse error) → **502** `{"detail": "<exception text>"}`. Observed with no
credentials configured:

```json
{"detail": "Your given address (https://login.microsoftonline.com/) should consist of an https url with hostname and a minimum of one segment in a path: e.g. https://login.microsoftonline.com/{tenant} …"}
```

---

## `POST /api/fetch/ecc`

Same as above, but reads the folder `SP_ECC_FOLDER_PATH` (default empty ⇒ document
library root), matches names starting with `MARC_DAP` / `MBEW_DAP` and ending
`.xlsx`/`.xls` (only `.xlsx` works — see [03-backend](03-backend.md#fetch_ecc_files---dict)),
reads the **first sheet** of each workbook, and writes
`{OUTPUT_DIR}/MARC/ECC/MARC_ECC_combined.csv` and
`{OUTPUT_DIR}/MBEW/ECC/MBEW_ECC_combined.csv`.

Response `message`: `"ECC DAP files fetched and merged."`. `combined_files[].name`
is `MARC_ECC` / `MBEW_ECC`, `source_type` is `"ECC"`. Errors: 502 as above
(this includes an `ImportError` if `openpyxl` is not installed).

---

## `GET /api/preview/{family}/{source_type}`

```bash
curl "http://localhost:8000/api/preview/MARC/S4?limit=2"
```

| Query param | Type | Default | Constraint |
|---|---|---|---|
| `limit` | int | 20 | 1 ≤ limit ≤ 200 |

**200 response** (`PreviewResponse`) **(observed)** — for `MARC/S4?limit=2`:

```json
{
  "name": "MARC_S4",
  "columns": ["PRODUCT", "werks", "DISMM", "DISPO", "…147 columns in total…"],
  "rows": [
    {"PRODUCT": "000000009687900109", "werks": "US30", "DISMM": "PD", "DISPO": "217", "…": "…"},
    {"PRODUCT": "000000007079871062", "werks": "US30", "DISMM": "ND", "DISPO": "S73", "…": "…"}
  ],
  "total_rows": 24577,
  "preview_row_count": 2
}
```

* `rows` is a list of column→value objects. Blank cells are `null`. Numbers appear as
  JSON numbers (pandas-inferred), e.g. `"MINBE": 40.0`, because the preview reads
  the CSV *without* `dtype=str`.
* `total_rows` counts all rows in the file, not only the preview.

**Errors**

| Status | When | Body (observed) |
|---|---|---|
| 404 | Unknown family | `{"detail": "Unknown family 'FOO'."}` |
| 404 | Unknown source type | `{"detail": "Unknown source type 'XYZ'."}` |
| 404 | Combined file not yet generated | `{"detail": "File for MARC S4 hasn't been created yet."}` |
| 422 | `limit` out of range | `{"detail":[{"type":"greater_than_equal","loc":["query","limit"],"msg":"Input should be greater than or equal to 1",…}]}` |
| 500 | Combined CSV is empty/corrupt (e.g. an earlier fetch matched no files) | plain "Internal Server Error" — `EmptyDataError` is not caught |

---

## `GET /api/download/{family}/{source_type}`

```bash
curl -OJ http://localhost:8000/api/download/MBEW/S4
```

Reads the entire stored CSV, converts it to an in-memory `.xlsx` and streams it.

**200 response (observed):** `Content-Type:
application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`,
`Content-Disposition: attachment; filename="MBEW_S4_combined.xlsx"`; ≈ 3.2 MB for the
committed MBEW S4 file (2.97 MB CSV).

**Errors:** 404 `{"detail": "File for FOO S4 not found."}` (unknown *or* not yet
created); 500 for anything else (e.g. missing `openpyxl`). Large files (the MARC ECC CSV
is 11.8 MB, ~29 k × 249) take noticeably longer and use much more memory than the CSV
size suggests, since the whole workbook is built in RAM.

---

## `GET /api/validate/latest`

```bash
curl "http://localhost:8000/api/validate/latest?sheet=MBEW"
```

| Query param | Type | Required | Values |
|---|---|---|---|
| `sheet` | string | yes | `MARC` or `MBEW` |

Locates the newest file matching `{sheet}_ECC_combined` in
`{OUTPUT_DIR}/{sheet}/ECC/` and `{sheet}_S4_combined` in `{OUTPUT_DIR}/{sheet}/S4/`,
reads them as strings, and runs the six rules
([05-validation-engine](05-validation-engine.md)).

**200 response** (`ValidationReportResponse`) **(observed, abridged)** — this is the
real result for the committed MBEW files, which shows the header-mismatch problem
described in [known issue #2](08-known-issues.md#2-validation-cannot-match-the-committed-data-header-and-value-mismatch):

```json
{
  "sheet": "MBEW",
  "key_fields": ["Product Number", "Valuation Area"],
  "ecc_row_count": 15746,
  "s4_row_count": 22615,
  "ecc_source": "MBEW_ECC_combined.csv",
  "s4_source": "MBEW_S4_combined.csv",
  "overall_status": "fail",
  "rules": [
    {"rule_id": "field_coverage", "name": "Field Coverage", "status": "fail",
     "summary": "41 of 41 mapped field(s) missing from the S/4 file.",
     "details": [{"missing_field": "Product Number"}, {"missing_field": "Valuation Area"}, "…up to 20…"]},
    {"rule_id": "mandatory_completeness", "name": "Mandatory Field Completeness", "status": "fail",
     "summary": "5 mandatory field(s) have blank values.",
     "details": [{"field": "Product Number", "issue": "column missing (see Field Coverage)"}, "…"]},
    {"rule_id": "type_length_conformance", "name": "Data Type & Length Conformance", "status": "pass",
     "summary": "All field values conform to the mapped data type and length.", "details": []},
    {"rule_id": "record_count", "name": "Record Count Reconciliation", "status": "fail",
     "summary": "Row count mismatch: 15746 ECC row(s) vs 22615 S/4 row(s) (+6869).",
     "details": [{"ecc_row_count": 15746, "s4_row_count": 22615, "delta": 6869}]},
    {"rule_id": "key_integrity", "name": "Key Integrity", "status": "warning",
     "summary": "Could not resolve key field 'Product Number' in both files - skipped.", "details": []},
    {"rule_id": "value_transformation", "name": "Field-Level Value Transformation Accuracy", "status": "warning",
     "summary": "Skipped - key fields could not be resolved (see Key Integrity).", "details": []}
  ]
}
```

Response field reference:

| Field | Type | Description |
|---|---|---|
| `sheet` | `"MARC"｜"MBEW"` | Echo of the request. |
| `key_fields` | string[] | Business-key columns (S/4 labels) used by rules 5–6. |
| `ecc_row_count` / `s4_row_count` | int | Rows in each frame. |
| `ecc_source` / `s4_source` | string | File basenames (latest mode) or original upload filenames. |
| `overall_status` | `"pass"｜"fail"｜"warning"` | `fail` if any rule failed; else `warning` if any warned; else `pass`. |
| `rules[]` | object | Always exactly six, in this order: `field_coverage`, `mandatory_completeness`, `type_length_conformance`, `record_count`, `key_integrity`, `value_transformation`. |
| `rules[].status` | `"pass"｜"fail"｜"warning"` | `warning` = the rule could not fully run. |
| `rules[].summary` | string | One-line human-readable outcome. |
| `rules[].details` | any[] | Rule-specific evidence, capped (see rules doc). Shape differs per rule. |

**Errors**

| Status | When | Body |
|---|---|---|
| 404 | Directory or file missing | `{"detail": "Directory not found: ./data/combined\\MBEW\\ECC"}` or `{"detail": "No file matching sheet 'MBEW_ECC_combined' found in …"}` |
| 422 | `sheet` missing or not MARC/MBEW | `{"detail":[{"type":"literal_error","loc":["query","sheet"],"msg":"Input should be 'MARC' or 'MBEW'",…}]}` |
| 500 | Unhandled exception in rules (e.g. duplicate keys, [known issue #3](08-known-issues.md#3-rule-6-crashes-on-duplicate-keys)) | "Internal Server Error" |

The 404 message for a missing directory uses the **relative** `SP_OUTPUT_DIR`
value and the OS path separator, so it can look like `./data/combined\MBEW\ECC`.

---

## `POST /api/validate/upload`

```bash
curl -X POST "http://localhost:8000/api/validate/upload?sheet=MARC" \
     -F "ecc_file=@MARC_DAP.xlsx" \
     -F "s4_file=@S_MARC_FreeText.csv"
```

| Part | Type | Required | Notes |
|---|---|---|---|
| `sheet` (query) | `MARC｜MBEW` | yes | |
| `ecc_file` (form file) | `.csv`, `.xlsx` or `.xls` | yes | The **ECC** side. Excel → first sheet. |
| `s4_file` (form file) | `.csv`, `.xlsx` or `.xls` | yes | The **S/4** side. |

Reader is chosen by extension (`.xlsx`/`.xls` → Excel; everything else → CSV);
all values are read as strings (`dtype=str`). Response is the same
`ValidationReportResponse`, with `ecc_source`/`s4_source` set to the uploaded
filenames (or `"uploaded ECC file"` / `"uploaded S4 file"` if a filename is absent).

| Status | When |
|---|---|
| 400 | `{"detail": "Could not read uploaded file(s): <pandas error>"}` — corrupt file, wrong format, `.xls` without `xlrd`, Excel without `openpyxl`. |
| 422 | Missing `sheet`, `ecc_file` or `s4_file` (body location errors listed per field). |
| 500 | Exception inside the rules. |

**(Observed)** Uploading two tiny 1-column CSVs returns 200 with
`overall_status: "fail"` and rules `field_coverage=fail, mandatory_completeness=fail,
type_length_conformance=pass, record_count=pass, key_integrity=warning,
value_transformation=warning` — i.e. unrelated files never error; they just fail.

---

## Cross-cutting behaviour

### CORS (observed)
* Preflight from `http://localhost:5173` → 200 with
  `access-control-allow-origin: http://localhost:5173`,
  `access-control-allow-credentials: true`.
* Preflight from any other origin → **400** and no `allow-origin` header.

### Error body shape
* Application errors: `{"detail": "<string>"}`.
* Validation errors (422): `{"detail": [ {type, loc, msg, input, ctx?}, … ]}` —
  the frontend's `body.detail || …` fallback would render this array as
  `[object Object]` (see [06-frontend](06-frontend.md#4-api-client-modules)).
* Unhandled 500s: FastAPI's plain-text `Internal Server Error` (not JSON), so the
  frontend falls back to `Request failed (500)`.

### Idempotency and concurrency
* `POST /api/fetch/*` overwrite the same files every call. Two simultaneous fetches
  race on the same output path; the last writer wins and a reader may briefly see a
  partially written CSV.
* GET endpoints are read-only.
