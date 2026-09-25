# GyanSys Migration Tool

Layered full-stack app for the ECC → S/4 migration workflow. This milestone
adds the **Data Fetch** tab: pulling `S_MARC#FreeText` and `S_MBEW#FreeText`
extracts from the SharePoint "Databricks Files" folder, merging each
family into one combined CSV, and previewing the result in the UI.

## Structure

```
gyansys-migration-tool/
├── backend/                    FastAPI, layered architecture
│   ├── app/
│   │   ├── api/routes/         HTTP endpoints (thin — no business logic)
│   │   │   └── fetch.py        POST /api/fetch, GET /api/preview/{name}
│   │   ├── services/           Business logic
│   │   │   ├── sharepoint_service.py   Graph API auth + list + download
│   │   │   └── merge_service.py        Combines matching files per group,
│   │   │                                reads back rows for preview
│   │   ├── models/             Pydantic schemas (API contracts)
│   │   ├── core/                Config / settings
│   │   └── main.py              App entrypoint + CORS
│   ├── requirements.txt
│   └── .env.example
│
└── frontend/                    Vite + React
    └── src/
        ├── components/
        │   ├── layout/          Header, NavBar, Footer (site chrome)
        │   └── data-fetch/      FetchActionCard, StatusPanel,
        │                        SummaryCard, PreviewTable
        ├── pages/                One page per tab (DataFetchPage, ...)
        ├── api/                  fetchFromSharePoint() / fetchPreview()
        └── App.jsx               Routes + layout wiring
```

## API endpoints

| Method | Path                  | Purpose                                               |
|--------|------------------------|--------------------------------------------------------|
| POST   | `/api/fetch`            | Pull + combine files, save locally, return per-file stats |
| GET    | `/api/preview/{name}`   | Return the first N rows (default 20) of an already-combined file, e.g. `/api/preview/MARC_combined?limit=20` |
| GET    | `/api/health`            | Basic liveness check                                    |

Interactive docs (Swagger UI) are available at `/docs` once the backend is running.

## Adding a new file family later

To combine another set of files (beyond MARC/MBEW), add one line to
`GROUPS` in `backend/app/services/merge_service.py` — no other code needs
to change, and it's automatically previewable too:

```python
GROUPS = {
    "MARC_combined": "S_MARC#FreeText",
    "MBEW_combined": "S_MBEW#FreeText",
    "MLAN_combined": "S_MLAN#FreeText",   # <- new
}
```

## Running it locally

### Backend
```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env       # then fill in SP_TENANT_ID / SP_CLIENT_ID / SP_CLIENT_SECRET
uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 — the "Data Fetch" tab is the default landing page.

## Notes

- The logo import in `Header.jsx` expects a file at
  `frontend/src/assets/logo.png`. If you swap in the real GyanSys logo
  under a different filename, update the `import logo from "..."` line in
  `Header.jsx` to match.
- The client secret goes in `backend/.env` (never committed — see
  `.gitignore`). Rotate any secret that has ever been pasted in plaintext
  anywhere outside that file.
- `SP_SITE_PATH` / `SP_FOLDER_PATH` in `.env` point at the
  "RO SharePoint Automation" site's "Databricks Files" folder by default —
  update if the source location changes.
- Combined CSVs are written to `backend/data/combined/` and are what the
  preview endpoint reads from — run a fetch at least once before trying
  to preview.
