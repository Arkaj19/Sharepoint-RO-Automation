# 10 — Own Logic: Import, Generation and Shadow Run

> Part of the [documentation set](README.md). This builds on [09 — Agentic architecture](09-agentic-architecture.md).

## 1. Goal

Stop depending on the Databricks views. The migration rules become ours: approved rules in the YAML mappings, maintained with Agent 1. The tool then **generates** the S/4 load data itself.

During the transition, Databricks is a parallel check. Every refresh can compare our output with theirs field by field (the *shadow run*). Cut over when the remaining gaps are explained and approved.

```
Databricks SQL ─┐                                        ┌─▶ Rule book (team layout)
rule workbook  ─┼─▶ import ─▶ proposal ─▶ review ─▶ YAML ─┼─▶ Generate S_MARC / S_MBEW ─▶ shadow compare vs Databricks
data (ECC, S4) ─┘   (scored against the data)             └─▶ Validation / Agent 1 (verifies imported rules)
```

## 2. Inputs

| Input | Where it comes from | Used for |
|---|---|---|
| ECC extracts: MARC, MBEW | SharePoint library root | Source rows |
| S/4 extracts: `S_MARC#…`, `S_MBEW#…` | SharePoint `Databricks Files` | Shadow comparison, evidence |
| **MARA** | SharePoint root (`MARA_DAP…`) | Material type, base unit, product hierarchy, deletion flag, scope |
| **MARM** | SharePoint root or `backend/data/incoming/` | EA-per-case conversion (prices, lot sizes) |
| **MARD** | SharePoint root or `backend/data/incoming/` | Storage location (plants 1000 / 1029) |
| MVKE | optional, not delivered yet | Completes the material scope (S_MARA) |
| `ekgrp_updated` | optional, not delivered yet | Purchasing-group overrides (EKGRP) |
| `1025_new_matnr` | optional, not delivered yet | Material renumbering (MATNR_new) |
| **Rule workbook** `Material-Master_Source.xlsx` | SharePoint root | Business intent per field (Logic Type / Logic), conflict check |
| **Databricks views** | `backend/reference_logic/databricks/*.sql` (committed) | Technical rules to import |
| **SAP labels seed** | `backend/reference_logic/sap_labels.yaml` | Technical field → extract column label |

**Refresh** fetches the supporting tables like any other dataset (versioned, current + 2 previous).
- Tables not on SharePoint are picked up from `backend/data/incoming/`.
- Optional tables that are missing are reported as `missing`. Nothing fails because of them: rules that need them report **input missing** and the field is left blank.

## 3. Extended rule vocabulary (YAML)

On top of what [09 §4](09-agentic-architecture.md#4-the-yaml-mapping-backendmappingsobjectyaml) describes:

| Construct | Example | Meaning |
|---|---|---|
| `dictionary` | `MARC: {BESKZ: Procurement type}` | Technical field → extract column label, per table |
| `source_ref` | `source_ref: MARC.DISMM` | Where a field reads from, in SAP terms (resolved through the dictionary) |
| `lookups` | `MARA` on `MARC.MATNR = MATNR` (strip zeros) | Left joins of supporting tables. Columns become refs like `MARA.MTART` |
| `ref` in conditions | `{ref: MARA.MTART, op: eq, value: FERT}` | Test any column, not only mapped fields |
| `route` step | branches of `IF … THEN 'US30'` | **Plant routing:** each ECC row goes to every plant a branch routes it to; rows with no route are not migrated |
| `TARGET.<field>` | `from_ref: TARGET.plant` | The routed plant (e.g. PRCTR = derived plant) |
| `from_ref` step | `{op: from_ref, ref: MARC.LGPRO}` | Take another column's value |
| `expr` step | `round(MBEW.STPRS / MARM_EA.UMREN, 2)` | Arithmetic in SQL syntax; blanks propagate like NULL |
| case with `steps` | `{when: …, steps: [expr]}` | A case result computed rather than constant |
| per-plant cases | `IF MARC.WERKS IN ('1000','1029') THEN …` | Rules that differ by plant block |
| `sql_null`, `numeric` | on conditions | SQL NULL semantics for blanks; `0.000 = 0` |

All comparisons go through one normalisation: the field's `compare` steps, then canonical numbers and dates. Validation, the shadow run, the ChangeSet and Agent 1's evidence therefore show the same numbers.

## 4. Importing the Databricks logic

Use the **Mapping** page → *Import as proposal*, or `POST /api/import/{MARC|MBEW}`. It takes about 10–30 s.

1. **Parse** the ten views (5 plant blocks × MARC / MBEW) with sqlglot and translate them:
   - columns become `source_ref` / `from_ref`;
   - literals become `constant`;
   - `CASE` becomes `case`;
   - arithmetic becomes `expr`;
   - `REGEXP_REPLACE(x,'^0+','')` becomes strip zeros;
   - `to_date` becomes `date`;
   - `base.derived_werks` becomes `TARGET.plant`;
   - the base CTE becomes `route` branches.

   Anything outside the vocabulary is reported as *untranslated* and never guessed. Today nothing is untranslated.
2. **Merge the plant blocks.** A field whose SQL is the same in every block gets one rule. Otherwise it gets per-plant cases.
3. **Dictionary.**
   - Curated labels are checked against the extract headers.
   - Passthrough fields are verified by value agreement with the S/4 output.
   - The rest are found by value matching.
   - Anything still unresolved is listed.
4. **Lookups, filters, scope.**
   - Joins become lookups.
   - WHERE conditions become filters.
   - The `INNER JOIN mm_S_MARA` becomes a scope filter built from the S_MARA rules: material in MARA, not deleted, not NVAL. MVKE completes it once it's delivered.
5. **Drift detection.** Commented-out `WHEN` lines in the routing are restored to build an *alternative* routing op. The two are offered as alternatives: accept exactly one.
6. **Scoring.** The tool generates with the imported rules and shadow-compares them with the current S/4 output.
   - Each field op's confidence is its measured agreement.
   - The routing ops carry the share of S/4 rows they reproduce.
7. **Conflicts with the workbook.** The workbook's Logic Type and quoted values are compared with the SQL.
   - Plant codes, number formats, "BLANK" wording and placeholders such as "-" are ignored.
   - A "Hardcoded" rule also conflicts when the SQL sets other values for some plants ("SQL also sets ['CG25']").
   - Differences are flagged, confidence is capped at 0.6, and the op goes to human review.
   - Bulk accept skips conflicts and alternatives.
8. **Only what differs is proposed.** The imported ops are reconciled with the current mapping.
   - Rules that are already in the mapping unchanged are not proposed again. They're listed under "Dropped", and the header counts them.
   - The exception is a rule with a workbook conflict: it is proposed again for a decision, and its After panel says "no change to the current rule - proposed again because it conflicts with the business workbook".
   - A filter whose condition changed is replaced (remove + add) instead of failing.
   - Every proposed op carries a before/after preview naming its field.
   - The key's compare step (strip leading zeros for zero-padded S/4 material numbers) is imported too.
   - A re-import after one SQL change is therefore a short proposal, and its apply touches only that rule.

**Measured on the live data (2026-09-29):**

| | MARC | MBEW |
|---|---|---|
| Operations | 161 | 58 |
| S/4 rows reproduced: SQL as shared → with commented lines restored | 76.0% → **98.0%** | 75.5% → **97.9%** |
| Fields matching the Databricks output exactly | **119 / 147** | **37 / 42** |
| Cell agreement | 99.6% | 99.4% |
| Waiting for an input | EKGRP (ekgrp_updated), MATNR_new (1025_new_matnr) | MATNR_new |
| Workbook conflicts | 5: MMSTA, LGPRO, LGFSB (the SQL has more cases than the workbook describes) and DISMM, PRCTR (workbook says Passthrough, SQL is Derived) | 0 |

The **~26 MARC / 4 MBEW fields that differ** are mostly drift: the S/4 files were built with an older version of the SQL than the one shared. Examples:
- MRP type: the SQL maps procurement type X → `ND`, but their output keeps `PD`;
- issue storage location for 1025: the SQL gives `PL01`, their output has `PL02`;
- lot-size rules, and a blank `DISGR` where the SQL passes it through.

The Output page lists each field with examples.

## 5. Generation and shadow run

Use the **Output** page → *Generate & compare*, or `POST /api/outputs/{obj}/generate`.
- The run executes lookups → routing → filters → field rules → `DISTINCT`.
- It writes `data/outputs/<OBJ>/<run>/S_<OBJ>.csv` and `report.json`.

The report contains:
- **rows:** Databricks rows reproduced, our rows confirmed, only-ours and only-theirs keys with examples;
- **fields:** match / mismatch / input missing, agreement, examples (ours vs Databricks);
- **filters:** rows removed per filter, or "input missing".

The rule book's Overview shows the latest shadow result per object.

## 6. Rule book in the team's layout

Each object sheet uses `Material-Master_Source.xlsx`'s columns:

*Sheet Name (with filters), Group Name, Field Description, Importance, Type, Length, Decimal, SAP Structure, ECC Table, SAP Field, **Logic Type** (Passthrough / Hardcoded / Derived), **Logic***

Governance columns follow:
- status, confidence, agreement with S/4, source, rule ID, version, approver;
- the workbook's own logic, and any conflict with it;
- pending proposal changes (yellow).

Extra sheets:
- **Plant-wise:** routing and per-plant rules, one column per plant block, like the team's plant comparison sheet;
- **Lookups:** with availability;
- **Dictionary**, **Value Maps**, **Filters**;
- **Change Log:** only the attributes that changed.

## 7. Agent 1 with imported rules

Imported rules carry provenance `databricks`. Agent 1's job for them is **verification**:
- The ChangeSet flags imported fields whose agreement drops.
- `get_reference_logic` gives the model the SQL per plant block, the workbook text, the conflict and the live agreement.
- The prompt tells it to explain drift and propose changes only with measured evidence. It must never swap an imported rule for an inferred one.

## 8. API additions

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/import/{obj}` | Import the Databricks views + workbook as a proposal (scored) |
| GET | `/api/import?object=` | Past imports: drift, row and field scores |
| POST | `/api/outputs/{obj}/generate` | Generate S_<OBJ> and shadow-compare |
| GET | `/api/outputs?object=` · `/api/outputs/{obj}/{run}` · `/…/download` | Runs, report, CSV |
| GET | `/api/snapshots` | Now also lists supporting tables (available / missing) |

## 9. Still open

1. **Which SQL is current?** The S/4 extracts match the SQL with the commented-out routing lines, and older field logic in about 26 MARC fields. The Databricks team should confirm, and ideally regenerate the S/4 files from the current SQL, so the shadow run compares like with like.
2. **MVKE, `ekgrp_updated`, `1025_new_matnr`:** once delivered to SharePoint or `data/incoming/`, the next refresh picks them up.
3. **Output format:** filling the Migration Cockpit template (the Q500 file's layout) is the next step. The generated CSV already has the S/4 column set.
4. **Other objects** (S_MARA, S_MVKE, …): the workbook covers 27 structures. Each needs its extracts in `ingest/sources.py`, a mapping YAML and (optionally) its Databricks view.
