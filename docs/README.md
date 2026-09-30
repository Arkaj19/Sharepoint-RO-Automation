# GyanSys Migration Tool — Documentation

Documentation for the **Sharepoint-RO-Automation** repository: a FastAPI + React app that
pulls SAP ECC and S/4 (Databricks) MARC/MBEW extracts from SharePoint via Microsoft Graph,
keeps versioned local copies, lets **Agent 1 (Rule Book agent)** propose YAML mapping updates
for human approval, generates the rule book and validates the ECC → S/4 conversion.

Docs 01–08 were written against commit `731e6cd`, before the agentic restructure.
[09 — Agentic architecture](09-agentic-architecture.md) describes the current codebase. Where
they disagree (fetch/merge services, the JSON mapping, `/api/fetch`), 09 is current.

## Read this first

> **Validation now works on the real data.** The explicit ECC/S4 columns in the YAML mapping,
> the key normalisation and the crosswalk are what Agent 1 bootstraps. Once those are approved,
> keys align (≈ 24 k MARC keys match on the live extracts) and values are compared field by
> field. See [known issue #2](08-known-issues.md#2-validation-cannot-match-the-committed-data-header-and-value-mismatch)
> for the history.

## Contents

| # | Document | Read it when you want to… |
|---|---|---|
| 01 | [Overview and architecture](01-overview-and-architecture.md) | understand what the system does, the domain terms, the layers, data-flow diagrams, and design decisions |
| 02 | [Setup, configuration and operations](02-setup-and-operations.md) | install, configure `.env`, run, operate, maintain the mapping, add a family, troubleshoot |
| 03 | [Backend reference](03-backend.md) | look up any backend module/function and its exact behaviour |
| 04 | [HTTP API reference](04-api-reference.md) | call the endpoints — parameters, real example responses, error bodies |
| 05 | [Validation engine](05-validation-engine.md) | understand the six rules, key handling, and what each check does and doesn't do |
| 06 | [Frontend reference](06-frontend.md) | work on the React app — routing, state, components, API client |
| 07 | [Data, mapping and file formats](07-data-and-mapping.md) | know what's on SharePoint, the mapping schema, and what the committed data looks like |
| 08 | [Known issues and recommendations](08-known-issues.md) | see verified defects, risks, and a suggested order of fixes |
| 09 | [Agentic architecture and Agent 1](09-agentic-architecture.md) | **the current structure**: snapshots, diff, YAML mapping, Agent 1, proposals, rule book, API |
| 10 | [Own logic: import, generation, shadow run](10-own-logic.md) | how the Databricks SQL and the rule workbook become our rules, how the tool generates S/4 data itself, and how it compares with Databricks |

## Quick start

```bash
# Backend (from backend/, Python 3.11)
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # SP_* credentials and AZURE_OPENAI_* for Agent 1
uvicorn app.main:app --reload --port 8000

# Frontend (from frontend/)
npm install && npm run dev    # http://localhost:5173  ->  Data -> "Refresh data"
```

## At a glance

```
Browser (React, :5173) --REST--> FastAPI (:8000) --Graph--> SharePoint (latest files only)
                                     |
                                     +-- backend/data/snapshots/<OBJ>/<SIDE>/<version>/   current + 2 previous
                                     +-- backend/data/{changesets,proposals,agent_runs,rulebooks}/
                                     +-- backend/mappings/<OBJ>.yaml                       the mapping (committed)
```

See [09 §9](09-agentic-architecture.md#9-api) for the full endpoint list.

## How these docs were verified

* Every source file (backend, frontend, config) was read in full.
* The real validator was run against the four committed CSVs; the read-only API endpoints
  were exercised with FastAPI's `TestClient`; the mapping JSON and CSVs were measured with pandas.
* With the `backend/.env` credentials, SharePoint was **listed read-only** (file names and
  sizes). No file was downloaded, no fetch endpoint was run, and nothing was written to
  SharePoint or to `backend/data/`.
* Those experiments ran in a throw-away virtual environment (Python 3.14, pandas 3.0), not
  the pinned pandas 2.2.2. Items whose behaviour depends on that difference are labelled
  in the text.
