# 07 — Data, Mapping and File Formats

> **Historical (pre-agentic).** This page describes the codebase at commit `731e6cd`. The fetch/merge services, the JSON mapping and several routes described here have been replaced - see [09 — Agentic architecture](09-agentic-architecture.md) for the current structure.

> Part of the [documentation set](README.md). Facts here come from reading the
> committed files and, where marked **(live)**, from a read-only listing of the real
> SharePoint site on 2026-09-29 using the credentials in `backend/.env`.
> No SharePoint file was downloaded or modified and no fetch endpoint was run.

## 1. Source data on SharePoint (live)

Site: `https://adam702.sharepoint.com/sites/ROSharePointAutomation`, default document
library.

### 1.1 S4 side — folder `Databricks Files` (10 files, all CSV)

| File | Size |
|---|---|
| `S_MARC#FreeText - 1021.csv` | 3,017,107 B |
| `S_MARC#FreeText - 1025.csv` | 1,189,487 B |
| `S_MARC#FreeText - 1028.csv` | 2,470,191 B |
| `S_MARC#FreeText - 1029.csv` | 186,740 B |
| `S_MARC#FreeText - 1030.csv` | 1,087,847 B |
| `S_MBEW#FreeText - 1021.csv` | 1,192,414 B |
| `S_MBEW#FreeText - 1025.csv` | 497,880 B |
| `S_MBEW#FreeText - 1028.csv` | 984,241 B |
| `S_MBEW#FreeText - 1029.csv` | 64,621 B |
| `S_MBEW#FreeText - 1030.csv` | 461,739 B |

Pattern: `S_{FAMILY}#FreeText - {plant}.csv` — **one file per family per (source) plant**,
five plants: 1021, 1025, 1028, 1029, 1030. All ten match the S4 prefixes in
`merge_service.S4_GROUPS`, so a fetch combines 5 + 5 files. (The plant in the filename
appears to be the ECC plant number; inside the files the plant column holds S/4 codes
like `US30`. That relationship is an inference — the file contents don't record which
part-file a row came from.)

### 1.2 ECC side — library root (7 items)

| Item | Matches? |
|---|---|
| `MARC_DAP 8.10.26.XLSX` (25.7 MB) | ✅ family MARC (`.lower()` makes the uppercase `.XLSX` extension match) |
| `MBEW_DAP 8.10.26.XLSX` (6.1 MB) | ✅ family MBEW |
| `MARA_DAP 8.10.26.XLSX` (16.6 MB) | ❌ not configured (no MARA family) |
| `Material-Master_Source.xlsx`, `Source data for Product - Q500 - Aug14 Excl Subcon & Kits - Part1.xlsx`, `Source data for Product-Blank Template.xlsx` | ❌ ignored |
| folder `Databricks Files` | ❌ folder (also returned by the listing; ignored because its name doesn't match a prefix) |

So the ECC side is **one workbook per family** (the `8.10.26` in the filename looks like
a date stamp, but its format is ambiguous and undocumented — treat it as an opaque version label). It
contains **all** ECC plants (13 distinct values in the committed extract), whereas the S4 side
covers five source-plant files.

> Because the ECC filename carries a date, a new extract with a different date still
> matches (`startswith("MARC_DAP")`). If two DAP workbooks for the same family are
> ever present in the library root, **both** are concatenated.

## 2. The mapping workbook and JSON

`backend/app/data/MARC_MBEW_Mappings.xlsx` → `backend/app/data/marc_mbew_mapping.json`
(pre-parsed; the runtime reads only the JSON).

### 2.1 Workbook layout

Two sheets, `MARC` (147 rows × 10 cols, title "Plant Data") and `MBEW` (42 rows × 10 cols,
title "Valuation Data"). Row 1 is a title row; rows 2 onward are fields (no column-header
row). Columns (as documented in `mapping_loader.py` and confirmed against the file):

| Col | Contents | JSON key |
|---|---|---|
| A | Free text; the **first field row** holds the sheet's "INNER CONDITION / OUTER CONDITION" SQL-filter description | `condition_notes` (sheet level) |
| B | UI group in the source workbook (e.g. `Key`, `MRP Data`, `Purchasing`); mostly blank | `group` |
| C | **S/4 field label** (friendly name, e.g. `Plant`, `MRP Type`) | `s4_field` |
| D | Text containing "mandatory" ⇒ mandatory | `mandatory` |
| E | Data type: `Text` / `Number` / `Date` | `data_type` |
| F | Length (Text) / total digits (Number) | `length` |
| G | Decimal places (Number) | `decimal` |
| H | Sheet code `S_MARC` / `S_MBEW` | `sheet` |
| I | ECC source table (e.g. `MARC`, `MARA`, `N/A`) | `source_table` |
| J | ECC technical field, optionally with a parenthesised note (e.g. `WERKS  (DERIVED_WERKS)`) | `source_field` + `source_field_note` |

Rows are skipped when column J or C is empty. `source_field` is the leading
`[A-Z0-9_]+` token of J; the note is the first `( … )` group.

### 2.2 JSON schema

```jsonc
{
  "MARC": {
    "condition_notes": "INNER CONDITION:\n • MARC.LVORM IS NULL\n • MARC.WERKS = '1021' …",
    "fields": [
      {
        "s4_field": "Product Number",   // friendly S/4 label
        "group": "Key",                 // or null
        "mandatory": true,
        "is_key_marker": true,          // group == "Key"
        "data_type": "Text",            // "Text" | "Number" | "Date" | null
        "length": 80,                   // number | null
        "decimal": null,                // number | null
        "sheet": "S_MARC",
        "source_table": "MARC",         // string | null; may be "N/A"
        "source_field": "PRODUCT",      // technical name
        "source_field_note": null       // e.g. "DERIVED_WERKS"
      }
      // …
    ]
  },
  "MBEW": { "condition_notes": "…", "fields": [ … ] }
}
```

Each `fields[i]` is unpacked directly into `FieldMapping(**field)`, so the JSON keys
must match the dataclass exactly.

### 2.3 What the mapping contains (computed from the JSON)

| | MARC | MBEW |
|---|---|---|
| Mapped fields | **146** | **41** |
| Mandatory | `Product Number`, `Plant` | `Product Number`, `Valuation Area`, `Valuation Class`, `Price Control`, `Currency` |
| `Key`-marked field | `Product Number` | `Product Number` |
| Derived second key (code constant) | `Plant` | `Valuation Area` |
| Composite key used by validation | `Product Number + Plant` | `Product Number + Valuation Area` |
| Data types | 102 Text / 41 Number / 3 Date | 21 Text / 16 Number / 4 Date |
| Source table `N/A` (no ECC source) | 25 | 7 |
| Fields with a `source_field_note` | 1 (`Plant` → `DERIVED_WERKS`) | 0 |
| Text fields with `length` = 1 (indicator flags) | 20 | 4 |

Notable source-table oddities: MARC has one field sourced from
`con_dev.s4_mdm.ekgrp_updated` (a Databricks table, i.e. a derived/enriched value);
MBEW has `MARC (via BASE CTE)`, `MBEW, MARA, MARM`, `MBEW, MARA` and one `null`.

`condition_notes` records the extraction filters (e.g. plant `1021`, `LVORM IS NULL`
deletion flags, `MTART <> 'UNBW'`, a `DERIVED_WERKS <> '1021'` exclusion). It is loaded
but never used by the validator (see [known issue #6](08-known-issues.md#6-condition_notes-are-loaded-but-unused-and-row-counts-are-compared-strictly)).
The bullet characters (`•`) are stored as `•` escapes in the JSON; some consoles
display them as `�`.

### 2.4 Key-field derivation

```python
get_key_fields(sheet):
    primary   = first field with is_key_marker  ("Product Number")
    secondary = _SECONDARY_KEY_FIELD[sheet]     # hard-coded "Plant" / "Valuation Area"
    return [primary, secondary]
```

The workbook flags only `Product Number` as `Key`; the second component is a code
constant, per the loader's comment ("by convention").

## 3. Local output layout

```
backend/data/combined/
  MARC/ECC/MARC_ECC_combined.csv   MARC/S4/MARC_S4_combined.csv
  MBEW/ECC/MBEW_ECC_combined.csv   MBEW/S4/MBEW_S4_combined.csv
```

CSV, UTF-8, header row, `index=False`, comma-separated, default pandas quoting.
Naming is fixed: `{family}_{source_type}_combined.csv`.

## 4. What the committed combined data looks like

Measured from the committed CSVs (pandas, all columns read as strings):

| File | Rows | Columns | First columns |
|---|---|---|---|
| `MARC/ECC` | 29 387 | 249 | `Material, Plant, Maintenance status, DF at plant level, Valuation Category, …` |
| `MARC/S4` | 24 577 | 147 | `PRODUCT, werks, DISMM, DISPO, MTVFP, MMSTA, …, MATNR_new` |
| `MBEW/ECC` | 15 746 | 108 | `Material, Valuation Area, Valuation Type, Del. flag val. type, Total Stock, …` |
| `MBEW/S4` | 22 615 | 42 | `PRODUCT, BWKEY, BWTAR, BWTTY, MLAST, BKLAS, …, MATNR_new` |

### 4.1 Header styles are inverted relative to the validator

| Test | MARC | MBEW |
|---|---|---|
| Mapping `source_field` (technical) found in **S4** headers | **146 / 146** | **41 / 41** |
| Mapping `source_field` found in **ECC** headers | 0 | 0 |
| Mapping `s4_field` (friendly label) found in **S4** headers | 0 | 0 |
| Mapping `s4_field` found in **ECC** headers | 48 | 24 |

In other words the S4 files use SAP technical names (matching the mapping's
`source_field` column exactly — including the S4 column `werks` in lower-case) and
the ECC workbooks use SAP's on-screen field labels (only a coincidental subset equal
to the mapping's friendly labels). The validator looks for them the other way round.
S4 also has one extra column not in the mapping: `MATNR_new`
(populated for 658 rows in each file, e.g. `Y100061`, `327898`).
ECC files contain pandas de-duplicated column names (`Batch management.1`,
`Setup time.1`, …; 7 in MARC, 11 in MBEW) because the workbook has repeated labels.

### 4.2 Key values differ between systems

| | ECC | S4 |
|---|---|---|
| Material format | un-padded, 6–10 chars (`100014`, `968890133`) | 18 chars, zero-padded (`000000009687900109`), with some short ones (4, 7, 10 chars) |
| Plant / valuation area codes | 13 codes: `1000, 1021, 1025, 1028, 1029, 1030, 1031, 1032, 1033, 1034, 1035, 1041, 1055` | 8 codes: `CA02, US27 … US33` |
| Unique materials (MARC) | 23 137 | 11 219 |
| Unique materials (MBEW) | 10 054 | 10 438 |
| Duplicate business keys | MARC 0, MBEW 0 | **MARC 111**, MBEW 0 |

Joining on raw material strings matches almost nothing (**614** MARC / **523** MBEW
materials overlap), but stripping leading zeros raises the overlap to
**10 695 / 9 939** — so material numbers are recoverable with zero-normalisation,
but **plants are re-coded** (`1021` → `US30`, …) and would need a crosswalk that
exists nowhere in this repo. The S4 MARC file's 111 duplicate keys would also crash
Rule 6 ([known issue #3](08-known-issues.md#3-rule-6-crashes-on-duplicate-keys)).

### 4.3 Row counts do not simply reconcile by plant

Restricting ECC to the five plants that have S4 files gives MARC **27 778** vs S4
**24 577** and MBEW **14 222** vs S4 **22 615**, so the Rule 4 mismatch is not fully
explained by plant scope. Why S4 MBEW has *more* rows than ECC is not answerable from
the repository (the mapping's `INNER CONDITION` logic, e.g. valuation-type splits,
may be responsible).

### 4.4 Value formatting

The S4 CSVs contain float-formatted numerics (`1.0`, `0.0`, `40.0`, `100.0`) because
`fetch_s4_files` reads each CSV without `dtype` and pandas promotes integer columns
that contain blanks to float. Validation compares values as trimmed strings, so an
ECC `1` will not equal an S4 `1.0`.

## 5. Data-type handling across the pipeline

| Stage | Reader | dtype behaviour |
|---|---|---|
| Fetch S4 | `pd.read_csv(..., low_memory=False)` | Types **inferred** per column; leading zeros in an all-numeric column would be lost (the committed `PRODUCT` column kept them, presumably because it contains non-numeric material numbers) |
| Fetch ECC | `pd.read_excel(..., engine="openpyxl")` | Types inferred from Excel cell types (numbers → int/float) |
| Preview / download | `pd.read_csv(path)` | Inferred again |
| Validation (latest) | `table_io.read_table` → `dtype=str` | Everything is a string |
| Validation (upload) | `pd.read_csv/read_excel(..., dtype=str)` | Everything is a string |

The CSV round-trip therefore can *lose* information before validation even starts
(leading zeros, numeric formatting). See [known issue #5](08-known-issues.md#5-fetch-does-not-preserve-text-formatting-dtypestr).
