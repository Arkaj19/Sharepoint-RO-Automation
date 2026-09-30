# GyanSys Migration Tool

FastAPI + React tool for the SAP ECC → S/4HANA data-migration workflow (MARC plant data, MBEW valuation data). It pulls the ECC and S/4 (Databricks) extracts from SharePoint, keeps a versioned local copy of each, and detects what changed. **Agent 1, the Rule Book agent**, then proposes updates to the YAML mapping. A person approves each change, and the tool generates the rule book and validates ECC → S/4 against the approved mapping.

> Full documentation: [`docs/`](docs/README.md). Start with [09 — Agentic architecture](docs/09-agentic-architecture.md)
> and [10 — Own logic](docs/10-own-logic.md).

## How it works

```
Refresh data ─▶ versioned snapshots ─▶ diff (ChangeSet) ─▶ Agent 1 (GPT-5 + tools) ─▶ proposal
                (current + 2 previous)                                                  │ accept / reject (G1, G2)
                                                                                        ▼
                             Validation ◀── Rule book (.xlsx) ◀────────────── YAML mapping (new version)
```

- **SharePoint** always holds the latest files only. The tool stores each changed download as a version in `backend/data/snapshots/`. It keeps the current version plus 2 previous ones, and zips older versions into `backend/data/archive/`.
- **The mapping** lives in `backend/mappings/MARC.yaml` and `MBEW.yaml`: columns on both sides, key, transforms, crosswalks, split rules, filters and provenance. It is the single source of truth.
- **Agent 1** proposes typed operations with a reason, a confidence and evidence. It never edits the YAML itself. Nothing is applied until someone accepts it on the Proposals page.
- **Own logic:** the Databricks views (`backend/reference_logic/databricks/`) and the business rule workbook can be imported as a proposal. Each rule is scored against the real S/4 output. Once approved, the tool **generates** S_MARC / S_MBEW itself and shadow-compares them with Databricks on the Output page.

## Structure

```
backend/
  app/
    connectors/     SharePoint (Microsoft Graph)
    ingest/ store/  datasets, readers, versioned snapshot store
    profiling/ diff/ inference/ transforms/   deterministic engines
    mapping/        YAML model, repository, typed operations
    agents/         llm gateway (Azure OpenAI), tool loop, rulebook_agent (Agent 1)
    proposals/ rulebook/ validation/ workflows/
    api/routes/     refresh, snapshots, changesets, proposals, mappings, rulebook, agent, validate
  mappings/         MARC.yaml, MBEW.yaml, history/, changelogs   (committed)
  data/             generated local state                         (git-ignored)
  scripts/ tests/
frontend/src/
  pages/            Data · Changes · Proposals · Mapping · Rule Book · Validation
```

## Running it locally

### Backend (Python 3.11)

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # SharePoint credentials + AZURE_OPENAI_* for Agent 1
uvicorn app.main:app --reload --port 8000
```

- Set `LLM_PROVIDER=none` to run Agent 1 without a model (deterministic proposals only).
- Swagger UI is at `http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

Open **Data** and click **Refresh data**. Then go to **Mapping → Import as proposal**, review it on **Proposals**, and generate on **Output**.

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest tests
```

## Adding a migration object

1. Add its two rows (ECC and S4) to `SOURCES` in `backend/app/ingest/sources.py`.
2. Add a `backend/mappings/<OBJECT>.yaml`.
3. Allow the object in the validate route's `SheetName`.
