# 05 — Validation Engine

> **Historical (pre-agentic).** This page describes the codebase at commit `731e6cd`. The fetch/merge services, the JSON mapping and several routes described here have been replaced - see [09 — Agentic architecture](09-agentic-architecture.md) for the current structure.

> Part of the [documentation set](README.md). Source:
> `backend/app/services/validation_service.py` (428 lines) with the mapping
> access layer in `mapping_loader.py`.

## 1. Purpose and design intent

The engine answers: **"was the ECC extract converted into the S/4 target file
correctly?"** Its stated design principle is that *every rule is derived from
the mapping workbook, not from hard-coded business knowledge*, so editing
`MARC_MBEW_Mappings.xlsx` changes what gets checked.

### The intended data contract

The module docstring and the `validate()` docstring define the contract the code
was written for:

| Frame | Column names the code expects | Example |
|---|---|---|
| `ecc_df` | **raw ECC technical field names** — matched against `FieldMapping.source_field` (then `source_field_note`) | `WERKS`, `DISMM`, `BKLAS`, `PRODUCT` |
| `s4_df` | **friendly S/4 field names** — matched against `FieldMapping.s4_field` | `Plant`, `MRP Type`, `Valuation Class` |

> ⚠️ **The committed data has the opposite header styles**: the ECC CSVs carry SAP
> friendly labels (`Material`, `Plant`, `Valuation Area`) and the S4 CSVs carry
> technical names (`PRODUCT`, `werks`, `BWKEY`). As a result the engine currently
> cannot resolve a single mapped column for either sheet. Details, evidence and
> options are in [known issue #2](08-known-issues.md#2-validation-cannot-match-the-committed-data-header-and-value-mismatch).
> The rule descriptions below explain the *designed* behaviour and call out what
> happens today.

## 2. Types

```python
class RuleStatus(str, Enum):  PASS="pass"; FAIL="fail"; WARNING="warning"

@dataclass RuleResult:        rule_id, name, status, summary, details: list[dict]
@dataclass ValidationReport:  sheet, key_fields, ecc_row_count, s4_row_count,
                              rules: list[RuleResult], overall_status
```

* `WARNING` means *the rule could not fully run* (e.g. a key column could not be
  resolved) — not that data is suspicious.
* `MAX_SAMPLE_ISSUES = 20` caps how many detail entries most rules return. Summaries
  always carry the true counts; only the `details` list is truncated.

## 3. Column resolution

All matching is **case-insensitive and whitespace-trimmed** via
`_normalized_map(columns)` → `{str(col).strip().upper(): original_col}`.

| Helper | Logic |
|---|---|
| `_resolve_ecc_column(ecc_df, fm)` | 1) `fm.source_field.upper()` in the normalised ECC columns → use it. 2) else `fm.source_field_note.upper()` (the parenthesised note, e.g. `DERIVED_WERKS`). 3) else `None`. |
| `_resolve_s4_column(s4_df, fm)` | `fm.s4_field.upper()` in the normalised S/4 columns, else `None`. |

Only **one** ECC field carries a note across both sheets: `Plant` →
`WERKS` (note `DERIVED_WERKS`), meaning "if `WERKS` isn't there, use the derived
plant column".

## 4. Composite key handling

Business keys are composite (`Product Number` + `Plant`, or `Product Number` +
`Valuation Area`). To avoid pandas' ambiguous `.loc[tuple, col]` behaviour on a
tuple-valued index, keys are collapsed into a single string:

```python
_KEY_SEP = "␟"                       # visible "unit separator" glyph ␟
_build_key_series(df, columns) = df[columns].astype(str)
                                   .apply(lambda r: SEP.join(v.strip() for v in r), axis=1)
_split_key(label)               = tuple(label.split(SEP))
```

Implications: keys are compared as **trimmed strings, exactly** — `"000123"` ≠ `"123"`;
NaN becomes the literal text `"nan"`; there is no zero-padding normalisation, no
case folding and no cross-system code translation (e.g. ECC plant `1021` vs S/4 plant
`US30` are simply different strings).

## 5. The six rules

Execution order in `validate()`: 1 → 2 → 3 → 4 → 5 → (6 only if 5 could resolve
keys). Rules 1–3 look only at the S/4 frame; 4–6 compare both.

### Rule 1 — Field Coverage (`field_coverage`)

*Question:* does every mapped S/4 field exist as a column in the S/4 file?

* `missing` = all mapped `s4_field`s for which `_resolve_s4_column` is `None`.
* **fail** if any missing → summary `"{n} of {total} mapped field(s) missing from the S/4 file."`,
  details `[{"missing_field": name}, …]` (≤ 20).
* **pass** → `"All {total} mapped fields are present in the S/4 file."`.
* Extra columns in the S/4 file (e.g. `MATNR_new`) are **not** reported.

*Today:* fails with 146/146 (MARC) and 41/41 (MBEW) missing.

### Rule 2 — Mandatory Field Completeness (`mandatory_completeness`)

*Question:* do fields flagged "mandatory for sheet" have any blanks?

* Fields come from `get_mandatory_fields(sheet)`:
  * MARC → `Product Number`, `Plant`
  * MBEW → `Product Number`, `Valuation Area`, `Valuation Class`, `Price Control`, `Currency`
* For each: resolve the S/4 column. If absent → issue
  `{"field", "issue": "column missing (see Field Coverage)"}`. Otherwise count values
  that are NaN **or** whose stripped string is `""`; any count > 0 →
  `{"field", "issue": "{n} blank value(s)"}`.
* **fail** if any issue → `"{n} mandatory field(s) have blank values."` (This wording
  also covers the *column-missing* case, which can be misleading.) **pass** otherwise.
* Only the S/4 side is checked; ECC blanks are not.

### Rule 3 — Data Type & Length Conformance (`type_length_conformance`)

*Question:* do S/4 values fit the mapped type and length?

For each mapped field whose S/4 column resolves:

1. **Length** — only for `data_type == "Text"` with a `length`:
   `series.astype(str).str.len() > int(length)` → count of over-long values →
   `"{n} value(s) exceed max length {L}"`.
2. **Type** (`_violates_type`) — skips NaN and blank strings, then:
   * `Number`: `float(text.replace(",", ""))` must succeed → else `"'x' is not numeric"`.
   * `Date`: passes if the value is a `date`/`datetime` instance (never true here since
     everything is read as string) else must parse with one of
     `%Y-%m-%d`, `%d.%m.%Y`, `%m/%d/%Y`, `%Y%m%d`.
   * `Text`: no type check.
   The count of violating values → `"{n} value(s) don't match expected type 'Number'"`.
3. **fail** → `"{k} field(s) have type or length violations."`, details
   `[{"field","issue"}, …]` (≤ 20 — note a single field can contribute two entries, one
   length and one type). **pass** otherwise.

What it does **not** check: `Number` total-digit `length` and `decimal` precision
(they are loaded from the mapping but unused), date values that parse in an
unlisted format, and ECC-side conformance.

Caveats:

* On the pinned pandas 2.2.x, `astype(str)` turns NaN into the 3-character text
  `"nan"`. For Text fields whose mapped length is 1 (20 MARC and 4 MBEW indicator
  fields such as `Indicator: Critical Part`), every blank cell would then be counted as
  exceeding length 1 — a **false positive**. *(Expected from pandas semantics; not
  reproduced here because the scratch environment used pandas 3.0, where
  string-dtype `astype(str)` preserves NaN.)*
* *Today* this rule reports **pass** vacuously, because no S/4 column resolves (Rule 1),
  so nothing is examined.

### Rule 4 — Record Count Reconciliation (`record_count`)

*Question:* do the row counts reconcile?

`len(ecc_df) == len(s4_df)` → **pass** `"Row counts match: N in both ECC and S/4."`;
otherwise **fail** `"Row count mismatch: E ECC row(s) vs S S/4 row(s) (±Δ)."` with
`details=[{"ecc_row_count","s4_row_count","delta"}]` where `delta = s4 − ecc`.

It is a strict equality check. The mapping workbook's `INNER CONDITION` filters
(e.g. `MARC.WERKS = '1021'`, `LVORM IS NULL`, `MTART <> 'UNBW'`) mean that legitimate
row-count differences are *expected* when the S/4 extract applies filters the ECC file
doesn't — but `get_condition_notes()` is not consulted, so the rule can't distinguish
"filtered by design" from "lost rows".

*Today (real result):* MARC 29 387 vs 24 577 (−4 810); MBEW 15 746 vs 22 615 (+6 869).
These are the only rule outcomes on the committed data that reflect the data itself
rather than a header mismatch.

### Rule 5 — Key Integrity (`key_integrity`)

*Question:* do business keys line up 1:1 with no orphans or duplicates?

1. `key_fields = get_key_fields(sheet)` → e.g. `["Product Number","Plant"]`.
2. For each key field, resolve the ECC column (technical name) **and** S/4 column
   (friendly name). If either is unresolved for *any* key field → return a **warning**
   `"Could not resolve key field '{kf}' in both files - skipped."` (and `None, None`
   keys, which makes Rule 6 skip).
3. Build a composite string key per row for each frame (§4).
4. Set arithmetic:
   * `missing_in_s4 = ecc_set − s4_set` → *"keys in ECC but missing from S/4"*
   * `orphans_in_s4 = s4_set − ecc_set` → *"keys in S/4 with no ECC source row"*
   * `duplicates` = S/4 keys with `value_counts() > 1` → *"duplicate keys in S/4 (should be unique)"*
5. Each finding → `{"issue", "count", "sample": [ {key_field: value, …} × ≤5 ]}`.
   Samples come from unordered set differences, so **which 5 are shown is arbitrary**.
6. **fail** → `"Key mismatch on Product Number + Plant: {n} issue type(s) found."`;
   **pass** → `"Every … key matches 1:1 between ECC and S/4, no duplicates."`

Notes: duplicate detection is **S/4 only** — duplicate ECC keys are never reported here.
The function returns `(RuleResult, ecc_keys, s4_keys)` so Rule 6 can reuse the key
series.

*Today:* warning on both sheets (the key columns can't be resolved).
*Even with headers fixed*, the key **values** don't line up between the committed files
(see [07-data-and-mapping §4](07-data-and-mapping.md#4-what-the-committed-combined-data-looks-like)):
ECC materials are un-padded (`100014`), S/4 are 18-char zero-padded
(`000000009687900109`), and plant codes differ entirely (`1021` vs `US30`).

### Rule 6 — Field-Level Value Transformation Accuracy (`value_transformation`)

*Question:* for records present on both sides, do the values agree field by field?

1. Only runs if Rule 5 produced keys; otherwise a **warning**
   `"Skipped - key fields could not be resolved (see Key Integrity)."` is emitted by
   `validate()`.
2. `mappings` = all mapped fields **except the key fields**.
3. Index both frames by their composite key; `common_keys` = index intersection, then
   de-duplicated (`~duplicated()`).
4. For each mapping where both the ECC and S/4 columns resolve (`fields_checked += 1`):
   compare `str(...).strip()` values **exactly** for every common key.
   Mismatch count → `field_mismatches[s4_field]`; up to 20 samples in total
   `{"key": {…}, "field", "ecc_value", "s4_value"}`.
5. **fail** → `"{m} of {checked} checked field(s) have mismatched values across {N} matched record(s)."`,
   details = `[{"field","mismatch_count"}, …]` + one final element
   `{"sample_mismatches": [...]}`. **pass** → `"All {checked} checked field(s) match exactly across {N} matched record(s)."`

Important semantics:

* Comparison is **verbatim string equality**. The rule performs **no transformation
  logic** — it doesn't apply unit conversions, code translations, or type
  normalisation. `"1"` vs `"1.0"`, `"0.00"` vs `"0"`, or `"20260225"` vs `"2026-02-25"`
  are mismatches. (The committed S/4 CSVs contain float-formatted values such as
  `1.0` and `0.0`.) The "transformation" in the name is really "identity".
* NaN on either side becomes `"nan"`, so two blanks match each other.
* **Crash on duplicate keys** — see [known issue #3](08-known-issues.md#3-rule-6-crashes-on-duplicate-keys):
  the shape mismatch raises `ValueError: operands could not be broadcast together…`
  (reproduced), returning HTTP 500 for the whole validation.
* Fields with `source_table == "N/A"` (25 in MARC, 7 in MBEW) have no ECC counterpart,
  yet the loader still stores a `source_field` for them; whether they resolve is purely a
  matter of whether the ECC file happens to have a column of that name.

## 6. Orchestration — `validate(sheet, ecc_df, s4_df)`

```python
rules = [rule_field_coverage(sheet, s4_df),
         rule_mandatory_completeness(sheet, s4_df),
         rule_type_length_conformance(sheet, s4_df),
         rule_record_count(sheet, ecc_df, s4_df)]
key_result, ecc_keys, s4_keys = rule_key_integrity(sheet, ecc_df, s4_df)
rules.append(key_result)
rules.append(rule_value_transformation(...)  if keys resolved  else <warning result>)
overall = FAIL if any FAIL else WARNING if any WARNING else PASS
```

`ValidationReport` also carries `key_fields`, `ecc_row_count` and `s4_row_count`.
Rules are independent except 5 → 6; a failure in one does not stop the others.

## 7. Worked example (designed behaviour)

Suppose MBEW ECC has `PRODUCT, BWKEY, BWTAR, BKLAS` and S/4 has `Product Number,
Valuation Area, Valuation Type, Valuation Class`:

| ECC row | S/4 row | Outcome |
|---|---|---|
| `A / 1010 / X / 3000` | `A / 1010 / X / 3000` | matches on all fields |
| `B / 1010 / Y / 3000` | `B / 1010 / Y / 7900` | Rule 6 mismatch on `Valuation Class` (`ecc_value 3000`, `s4_value 7900`) |
| `C / 1010 / …` | *(absent)* | Rule 5: key in ECC missing from S/4; Rule 4: count differs |
| *(absent)* | `D / 1010 / …` | Rule 5: orphan in S/4 |
| — | `A / 1010 / X / …` twice | Rule 5: duplicate key in S/4; **Rule 6 crashes** |

## 8. Performance

`_build_key_series` uses a row-wise Python `apply` (O(n) Python calls per frame);
Rule 3's type check iterates each S/4 column value-by-value in Python; Rule 6 uses
vectorised comparison per field. There is no caching between requests, so each click
re-reads and re-processes both files.

Measured on the development machine (pandas 3.0, Python 3.14): reading the two MARC
CSVs as strings takes ≈ 0.4 s and the whole `validate()` call takes ≈ 0.01 s *as the
rules run today* — because nothing resolves, Rules 3, 5 and 6 do almost no work.
Building one composite-key series for 29 000 rows takes ≈ 0.33 s, so once headers
are fixed expect roughly a second or two for MARC (two key builds, plus the
per-value Python loop in Rule 3 over ~146 columns). That last figure is an estimate,
not a measurement.

## 9. Extending the engine

* **New rule:** add a `rule_*` function returning `RuleResult`, append it in
  `validate()`. The frontend needs no change — it renders whatever
  `rules[]` contains (keyed by `rule_id`) and recursively renders `details`.
* **New sheet:** see [02 §7.2](02-setup-and-operations.md#72-add-another-file-family-eg-mlan).
* **Fixing the header contract:** see the options in
  [known issue #2](08-known-issues.md#2-validation-cannot-match-the-committed-data-header-and-value-mismatch).
