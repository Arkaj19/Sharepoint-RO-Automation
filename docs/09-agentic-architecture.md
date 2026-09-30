# 09 — Agentic Architecture and Agent 1 (Rule Book agent)

> Part of the [documentation set](README.md). This describes the agentic restructure, which replaces the fetch/merge services and the JSON mapping described in docs 01–07. Where this doc and the older ones disagree, this one is current.

## 1. Why

Previously, a changed extract on SharePoint meant fixing the mapping by hand:
- the mapping lived in an Excel workbook, converted once to JSON;
- every fetch overwrote the combined CSV, so there was no history and no diff;
- validation could not match the data at all ([known issue #2](08-known-issues.md#2-validation-cannot-match-the-committed-data-header-and-value-mismatch)).

The target architecture (*GyanSys Agentic Migration Tool – Detailed Architecture* and `GyanSys-Option1-Architecture.drawio`) defines **Agent 1 – Rule Book agent**. It profiles the data, drafts maps and rules, and writes the rule book, with human gates **G1** (mappings) and **G2** (rules). This phase implements Agent 1 only.

```
Refresh data ─▶ versioned snapshots ─▶ deterministic diff ─▶ Agent 1 ─▶ proposal ─▶ human accept/reject
   (UI)          (current + 2 prev)      (ChangeSet)          (GPT-5)     (typed ops)        │
                                                                                            ▼
                         validation ◀── rule book (.xlsx) ◀──────────── YAML mapping (new version)
```

## 2. Principles, carried over from the architecture doc

| Principle | How the code honours it |
|---|---|
| **Agents decide and explain; tools execute.** | All counting, joining, diffing and matching is pandas code (`diff/`, `inference/`, `transforms/`). The LLM only calls tools and emits **typed operations** (`mapping/ops.py`). It never edits the YAML. |
| **Deterministic by default.** | `agents/rulebook_agent/candidates.py` builds the proposal from measured evidence, with or without a model. Confidence is computed from metrics. The model reviews, drops, adjusts and adds. |
| **Human gates.** | An operation reaches the YAML only after a person accepts it and clicks **Apply**. G1 ops apply before G2 ops. An op whose dependency was rejected becomes `stale`. |
| **Independent, shared interpretation.** | `transforms/engine.py` is the only interpreter of mapping rules. Validation, inference, the agent's dry runs and the rule book all use it, so they cannot disagree. |
| **Auditable.** | Every agent run logs its prompt hash, tool calls, token usage and outcome (`data/agent_runs/`). Every applied op goes to `mappings/<OBJ>.changelog.jsonl`. Every mapping version is kept in `mappings/history/`. |

## 3. Codebase structure

```
backend/
  app/
    main.py                 FastAPI app + routers
    core/                   config.py (env), paths.py (all locations), jsonio.py (atomic writes), locks.py
    connectors/sharepoint.py  Graph client: token, site, list (with eTag/cTag/hash), download; timeouts, 429 retry
    ingest/                 sources.py (which files make up each dataset), readers.py (text-only reads),
                            combine.py (concat + __src_file/__src_part lineage)
    store/                  snapshot_store.py (versions, retention, archive), preview.py (preview / xlsx)
    profiling/profiler.py   per-column profile, stored with every version
    diff/                   datasets.py (file/schema/profile/key diffs), changeset.py (impact tags, mapping health)
    inference/              keys.py (key discovery + row alignment), column_match.py, value_maps.py
                            (crosswalks, fan-out, splits), evaluate.py (dry runs)
    transforms/engine.py    executes transform steps, conditions, filters, fan-out
    mapping/                model.py (YAML schema), repository.py (load/save/history/changelog),
                            ops.py (typed operations), render.py (human-readable rules), compat.py
    proposals/service.py    review queue: create, decide, apply
    rulebook/writer.py      rule book .xlsx from the YAML
    validation/service.py   six checks on YAML semantics
    agents/
      llm/                  gateway.py (interface), azure_openai.py, fake.py (tests)
      runtime/              loop.py (tool-calling loop), tools.py (registry), run_log.py, masking.py
      rulebook_agent/       agent.py (entry), candidates.py (deterministic proposer), tools.py (21 tools),
                            prompts/system.md
    workflows/refresh.py    Refresh data: list → download → combine → profile → diff → agent
    schemas/                request/response models
    api/routes/             refresh, snapshots, changesets, proposals, mappings, rulebook, agent_runs, validate
  mappings/                 MARC.yaml, MBEW.yaml, history/, *.changelog.jsonl   ← committed
  scripts/                  migrate_mapping_to_yaml.py, import_legacy_combined.py
  data/                     snapshots/, archive/, changesets/, proposals/, agent_runs/, rulebooks/,
                            refresh_runs/   ← generated, git-ignored
  tests/                    unit/, agent/, e2e/, fixtures/

frontend/src/
  api/                      client.js, agentApi.js, validateApi.js
  pages/                    DataPage, ChangesPage, ProposalsPage, MappingPage, RuleBookPage, ValidationPage
  components/common/        ui.jsx (cards, badges, tabs...), PreviewTable.jsx
```

Later agents (e.g. Agent 2, the code generator) go in `agents/<name>_agent/` and reuse `agents/llm` and `agents/runtime`. The deterministic engines have no agent-specific code.

## 4. The YAML mapping (`backend/mappings/<OBJECT>.yaml`)

The single source of truth: the rule book and validation are both derived from it. The model is `app/mapping/model.py`.

```yaml
schema_version: 1
object: MARC
mapping_version: 2                 # bumped on every Apply
content_hash: sha256:...           # detects edits made outside the app
key:
  fields: [product_number, plant]
filters:
  - id: F-MARC-plant-scope
    side: ecc
    expr: {field: plant, op: in, values: ['1021', '1025', '1028', '1029', '1030']}
value_maps:
  plant_crosswalk:                 # several entries with one `from` = fan-out
    on_missing: flag
    entries:
      - {from: '1021', to: US27}
      - {from: '1021', to: US30}
      - {from: '1025', to: US29}
fields:
  - id: product_number
    s4_label: Product Number
    s4_column: PRODUCT             # exact S/4 header (technical name)
    ecc_column: Material           # exact ECC header (screen label)
    compare: [{op: strip_leading_zeros}]
    provenance: {status: approved, confidence: 0.95, rule_id: R-MARC-0002, version: 2, ...}
  - id: plant
    s4_column: werks
    ecc_column: Plant
    transform: [{op: value_map, map: plant_crosswalk}]
  - id: procurement_type
    s4_column: BESKZ
    ecc_column: Procurement type
    transform:
      - op: case                   # split rule on the fan-out target
        cases:
          - when: {all: [{field: procurement_type, op: eq, value: E},
                         {field: plant, on: target, op: eq, value: US30}]}
            value: F
```

- **`transform`** turns the ECC value into the expected S/4 value. **`compare`** normalises both sides before comparing.
- **Steps:** `strip`, `upper`, `lower`, `strip_leading_zeros`, `zfill`, `value_map`, `case`, `default`, `constant`, `number` (`1.0` = `1`), `date`.
- **Conditions:** `eq`, `ne`, `in`, `not_in`, `is_null`, `not_null`, grouped with `all` / `any`. A condition names a **field id**. `on: target` tests the value a fan-out map produced.
- **Fan-out:** one ECC row becomes one S/4 row per target (ECC plant 1021 → US27 and US30). It is only allowed as the first step of a key field. `engine.prepare_ecc` expands the rows.
- **Provenance status:** `migrated` means imported from the old workbook and not yet reviewed. `approved` means applied from an accepted proposal. Drafts exist only in proposals.
- **Editing by hand is allowed.** Comments survive app saves (ruamel round-trip). A hand edit is detected via `content_hash` and recorded in the changelog at the next Apply.
- **Migration:** `python -m scripts.migrate_mapping_to_yaml` rebuilt both files from the old JSON (kept as `tests/fixtures/legacy_marc_mbew_mapping.json`). A golden test proves nothing was lost. All 146 / 41 `s4_column`s matched. `ecc_column` starts empty; Agent 1's bootstrap proposes it.

## 5. Versioned snapshots (`backend/data/snapshots/`)

A dataset is one (object, side): `MARC/ECC`, `MARC/S4`, `MBEW/ECC`, `MBEW/S4`.

```
data/snapshots/MARC/S4/versions.json            {"current": "...", "previous": ["...", "..."]}
data/snapshots/MARC/S4/<version>/manifest.json  SharePoint ids, eTag/cTag, size, hashes, sha256, rows
data/snapshots/MARC/S4/<version>/raw/           files as downloaded
data/snapshots/MARC/S4/<version>/combined.csv   all parts, text only, + __src_file / __src_part
data/snapshots/MARC/S4/<version>/profile.json
data/archive/MARC/S4/<version>.zip              older than current + SNAPSHOT_KEEP_PREVIOUS (2)
```

**Change detection, cheapest first:**
1. **Listing fingerprint** (name, id, quickXorHash or cTag, size). If it is unchanged, nothing is downloaded.
2. **Byte comparison** (sha256) after download. Identical bytes mean the new version is discarded.
3. If no dataset changed, the refresh ends as `no_changes` and Agent 1 is not run.
   - **Force** skips the fingerprint check, so every file is downloaded and read again.
   - It then re-runs the diff and Agent 1 for every object.
   - Identical bytes still never create a new version ("forced re-read: file contents identical, current version kept"), so a forced refresh can't push real history out of retention.
4. If no file matches, the dataset is reported as an error and **the current version is kept**. This fixes issue #4.
   - An *optional* supporting table that was stored before and has since disappeared from the library gets the status **kept**, plus a run **warning**: "…the file is no longer in …; rules keep using the stored version v… (stored …)".
   - The Supporting tables card shows it as "stored copy only".

**Local source mode.** Set `SOURCE_MODE=local` and `LOCAL_SOURCE_ROOT` to a folder that mirrors the library (ECC files at its root, `Databricks Files/` for the S/4 CSVs). The extracts are then read from disk instead of SharePoint.
- Item ids are relative to that folder, so a copied folder keeps its fingerprints.
- This is how the end-to-end test harness runs its sandboxes.

`__src_part` is the part label from the file name (`S_MARC#FreeText - 1021.csv` → `1021`). It is the strongest evidence for the plant crosswalk.

## 6. Diff and ChangeSet (`backend/data/changesets/`)

`diff/changeset.build(object)` compares previous → current per side and adds a cross-side health check:

- **changes**
  - files added, removed or changed;
  - columns added or removed, rename candidates (name + values + profile score), reorders;
  - row count, blank rate, new or removed code values, type drift, longer values;
  - keys added or removed, duplicates, per-column changed cells.
  
  Each change is tagged with the mapped fields and value maps it affects, then ranked.
- **alignment**
  - how current ECC and S/4 rows pair on the business key;
  - the key setup used, including a provisional crosswalk when none is approved yet.
- **mapping_health**
  - fields without an ECC column;
  - mapped columns that no longer exist;
  - unmapped columns;
  - crosswalk gaps;
  - fields whose values no longer agree;
  - expected vs actual row counts.

**Supporting tables.** A refresh in which a supporting table changed also diffs that table for every object whose lookups read it.
- The diff covers files, schema, profile, and cells, keyed like the lookup join (e.g. MARM on Material + unit).
- It appears under side `REF/<TABLE>`, with impacts `lookup:<id>` plus every field whose rule reads the lookup.
- A table delivered for the first time is recorded as `table_added`.
- A change to the business rule workbook is recorded as `workbook_changed`, with a hint to re-import the logic. Conflicts are found by the import.

ECC columns are matched to fields the same way the engine reads them: an explicit `ecc_column`, or `source_ref` through the dictionary. Imported fields are tagged too.

The object's first change set (nothing to compare, no earlier change set) has `baseline: true`, so the mapping health drives a **bootstrap** proposal. Every later change set is incremental, even when only a supporting table changed.

## 7. Agent 1

**Flow (`agents/rulebook_agent/agent.run`):**
1. Deterministic candidates from the evidence (`candidates.py`):
   - key column and zero-stripping normalisation (overlap of distinct values);
   - plant / valuation-area crosswalk with fan-out (lineage, or the share of each S/4 code's materials that each ECC code covers);
   - ECC column for every field (value agreement on aligned rows + label similarity, one-to-one);
   - `number` / `date` compares where the only differences are formatting;
   - value maps for consistently re-coded values;
   - split rules where one ECC value becomes several S/4 values (the discriminator can be a fan-out target);
   - ignore entries for S/4 columns not in the mapping;
   - a scope filter for ECC codes with no S/4 counterpart;
   - incremental ops: follow renames, extend crosswalks, widen lengths.
2. If `LLM_PROVIDER=azure`, GPT-5 reviews the draft through 21 tools:
   - **read:** mapping, ChangeSet, profiles, samples, paired rows;
   - **measure:** column matches, value maps, splits, crosswalks, dry-run evaluation;
   - **edit the draft:** `add_ops`, `drop_candidates`, `adjust_candidates`, `validate_ops`;
   - **finish:** `submit_proposal`.
3. Every operation is validated in order against the mapping and the current headers: schema, column existence, cross references and a dry-run apply. Invalid ones are dropped, with the reason logged.
4. The result is stored as one proposal (`data/proposals/`). Older open proposals for the object are superseded.

**Guardrails:**
- `LLM_MAX_STEPS` (25) and `LLM_RUN_TOKEN_BUDGET`.
- Samples: at most 50 rows per call and 200 per run. Unmasked by policy; set `LLM_MASKING=true` to use pseudonyms.
- Tool results are capped at about 12k characters.
- Any model error or early stop keeps the deterministic proposal, with a note in its summary.

**Measured on the committed data** (bootstrap, deterministic):

| | MARC | MBEW |
|---|---|---|
| ECC key column found | `Material`, strip leading zeros, 10,695 shared | `Material`, 9,939 shared |
| Crosswalk (coverage ≥ 97%) | 1021→US27+US30, 1025→US29, 1028→US28+US31, 1029→CA02, 1030→US32+US33 | same pattern |
| S/4 rows aligned to an ECC row | 97% | 98% |
| Split rule found | procurement type E→F in target plants US30 / US33, 99.8% of rows explained (the doc's "1021 → US27 E / US30 F" rule) | — |
| Proposal | 103 ops (79 column aliases, 20 transforms, …); 67 fields left for a human | 43 ops; 13 left for a human |

**Live run with GPT-5** (Azure deployment `gpt-5`, MBEW, after a real SharePoint refresh):
- 12 steps, 61 tool calls (batched), about 348k tokens, 40 sample rows, about 7 minutes. Ended with `submit_proposal`.
- The model dropped 8 candidates: label-only aliases on 100%-blank ECC columns, and a redundant filter.
- It adjusted 5 confidences, and added 2 aliases and 1 date compare.
- It flagged two real issues for a human:
  - S/4 standard price (STPRS) is ≈ ½ or ⅙ of the ECC value where the price unit (PEINH) is 100. This looks like a price-unit conversion rule.
  - S/4 `BKLAS` holds `ROH` / `FERT`-like values, which is suspicious for a valuation class.

Loop safeguards learned from the first live runs:
- **Wrap-up phase:** with 5 steps left, or 75% of the token budget used, only the finishing tools remain.
- The last step is reserved for `submit_proposal`.
- `add_ops` rejects duplicates of existing candidates.
- The prompt asks the model to batch independent tool calls.

## 8. Review, apply, rule book

- **Proposals page:**
  - G1 and G2 tabs; each operation shows before/after, confidence, reason and evidence;
  - Accept / Reject / Undo per op, or "Accept all pending ≥ 90%";
  - **Apply accepted** writes one new mapping version.
- **Rule Book page:** generates `RuleBook_<ts>_MARCv2_MBEWv3.xlsx`. It has sheets Overview, one per object, Value Maps, Filters and Change Log. Optionally, pending ops appear as highlighted Draft rows.
- **Validation page:** the same six checks, now on the YAML semantics:
  - explicit columns on both sides;
  - key normalisation and fan-out;
  - filters before the row count;
  - transform-then-compare per field;
  - duplicate-safe keys (fixes issue #3);
  - each rule isolated.

## 9. API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/refresh` `{force, objects, run_agent}` | start Refresh data (202, background) |
| GET | `/api/refresh`, `/api/refresh/{id}` | runs, stage progress |
| GET | `/api/snapshots` | live and archived versions per dataset |
| GET | `/api/snapshots/{obj}/{side}/{version}/preview` · `/download` · `/manifest` | per-version access (`current`, `previous` or an id) |
| GET | `/api/changesets?object=` · `/api/changesets/{id}` | diffs |
| GET | `/api/proposals?object=&status=` · `/api/proposals/{id}` | review queue |
| POST | `/api/proposals/{id}/decisions` · `/apply` | accept/reject; write accepted ops to YAML |
| GET | `/api/mappings` · `/{obj}` · `/{obj}/yaml` · `/{obj}/versions` · `/{obj}/diff?from_version=` · `/{obj}/changelog` | mapping views |
| POST | `/api/agent/run` `{object}` | re-run Agent 1 without a refresh |
| GET | `/api/agent/runs?object=` · `/{id}` · `/{id}/transcript` | audit |
| POST/GET | `/api/rulebook` · `/api/rulebook/{name\|latest}/download` | rule book |
| GET/POST | `/api/validate/latest?sheet=` · `/api/validate/upload?sheet=` | validation |

The old `/api/fetch/*`, `/api/preview/*` and `/api/download/*` remain as deprecated aliases.

## 10. Running and testing

```bash
cd backend
pip install -r requirements-dev.txt
# first run: click "Refresh data" - the first SharePoint download is the baseline.
# (offline only: python -m scripts.import_legacy_combined imports the old combined CSVs;
#  they are lossy - numbers were reformatted - so later diffs against them are noisy)
uvicorn app.main:app --reload --port 8000
python -m pytest tests                       # 35 tests; the e2e test runs once data/snapshots exists
```

Set `LLM_PROVIDER=none` to run Agent 1 without a model (deterministic proposals only).

## 11. Open items

> The own-logic extension (supporting tables, Databricks/workbook import, generation, shadow run, team-format rule book) is described in [10 — Own logic](10-own-logic.md).


- **Row counts.** ECC rows after fan-out and the scope filter still exceed the S/4 rows (e.g. MBEW 24,360 expected vs 22,615). Not every material fans out to both targets, and the rule for that is not in the data or the condition notes. It needs the migration owners. The agent reports the gap and does not guess a rule.
- **About 45% of MARC fields have no confident ECC column.** 48 are weak matches and 19 have no candidate, mostly derived fields (`source_table: N/A`) or columns that are empty in the data. The LLM review and human decisions handle these.
- **No API authentication.** `decided_by` is self-declared (issue #7).
- The SharePoint rule-book upload and the orchestrator's other gates (G3–G5) are out of scope for this phase.
