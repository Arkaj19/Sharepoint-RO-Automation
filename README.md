# GyanSys Migration Tool

Layered full-stack app for the ECC → S/4 migration workflow. This milestone
adds the **Data Fetch** tab: pulling `S_MARC#FreeText` and `S_MBEW#FreeText`
extracts from the SharePoint "Databricks Files" folder and merging each
family into one combined CSV.

## Structure

```
gyansys-migration-tool/
├── backend/                    FastAPI, layered architecture
│   ├── app/
│   │   ├── api/routes/         HTTP endpoints (thin — no business logic)
│   │   │   └── fetch.py        POST /api/fetch
│   │   ├── services/           Business logic
│   │   │   ├── sharepoint_service.py   Graph API auth + list + download
│   │   │   └── merge_service.py        Combines matching files per group
│   │   ├── models/             Pydantic schemas (API contracts)
│   │   ├── core/                Config / settings
│   │   └── main.py              App entrypoint + CORS
│   ├── requirements.txt
│   └── .env.example
│
└── frontend/                    Vite + React
    └── src/
        ├── components/
        │   ├── layout/          Header, Navbar, Footer (site chrome)
        │   └── data-fetch/      FetchActionCard, StatusPanel, SummaryCard
        ├── pages/                One page per tab (DataFetchPage, ...)
        ├── api/                  fetch wrappers for the backend
        └── App.jsx               Routes + layout wiring
```

## Adding a new file family later

To combine another set of files (beyond MARC/MBEW), add one line to
`GROUPS` in `backend/app/services/merge_service.py` — no other code needs
to change:

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

- Replace `frontend/src/assets/gyansys-logo.png` with the real logo — a
  transparent placeholder is checked in so the build doesn't break.
- The client secret goes in `backend/.env` (never committed — see
  `.gitignore`). Rotate any secret that has ever been pasted in plaintext
  anywhere outside that file.
- `SP_SITE_PATH` / `SP_FOLDER_PATH` in `.env` point at the new
  "RO SharePoint Automation" site's "Databricks Files" folder by default —
  update if the source location changes.
