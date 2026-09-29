# GyanSys Migration Tool — Documentation

Documentation for the **Sharepoint-RO-Automation** repository: a FastAPI + React app that
pulls SAP ECC and S/4 (Databricks) MARC/MBEW extracts from SharePoint via Microsoft Graph,
merges them into combined CSVs, and validates the ECC → S/4 conversion against a
field-mapping workbook.

Written against commit `731e6cd` (2026-09-29). It supersedes the root `README.md`, which is
partly out of date (see [known issue #12](08-known-issues.md#12-repository-hygiene-and-stale-content)).

## Read this first

> **The Validation tab does not currently produce a meaningful result on the committed
> data.** The validator expects ECC files with technical field names and S/4 files with
> friendly names; the real files are the other way round, and the key values (material
> padding, plant codes) also differ. Every mapped field is reported missing.
> See [known issue #2](08-known-issues.md#2-validation-cannot-match-the-committed-data-header-and-value-mismatch).
>
> Also: `openpyxl` is required but missing from `requirements.txt`
> ([issue #1](08-known-issues.md#1-openpyxl-is-missing-from-requirementstxt)).

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

## Quick start

```bash
# Backend (from backend/, Python 3.11 recommended)
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt openpyxl
copy .env.example .env        # fill in SP_TENANT_ID / SP_CLIENT_ID / SP_CLIENT_SECRET
uvicorn app.main:app --reload --port 8000

# Frontend (from frontend/)
npm install && npm run dev    # http://localhost:5173
```

## At a glance

```
Browser (React, :5173) ──REST──▶ FastAPI (:8000) ──Graph──▶ SharePoint
                                     │
                                     └── backend/data/combined/{MARC,MBEW}/{ECC,S4}/*_combined.csv
```

| Endpoint | Purpose |
|---|---|
| `POST /api/fetch/ecc` · `POST /api/fetch/s4` | pull + merge source files, save combined CSVs |
| `GET /api/preview/{family}/{source_type}` | first N rows of a combined CSV |
| `GET /api/download/{family}/{source_type}` | combined CSV as `.xlsx` |
| `GET /api/validate/latest?sheet=` · `POST /api/validate/upload?sheet=` | run the six validation rules |
| `GET /api/health` | liveness |

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
