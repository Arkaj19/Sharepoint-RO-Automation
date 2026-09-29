# 02 — Setup, Configuration and Operations

> Part of the [documentation set](README.md).

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11** (recommended) | The pinned `pandas==2.2.2` publishes wheels only up to Python 3.12. On 3.13+/3.14 `pip install -r requirements.txt` will try to build pandas from source and fail. |
| **Node.js 18+** and npm | Required by Vite 5. |
| **An Azure AD app registration** | Used for the client-credentials flow. Needs a tenant ID, client ID and client secret (see §3). |
| **Read access to the SharePoint site** | Default site: `adam702.sharepoint.com` → `/sites/ROSharePointAutomation`. |

## 2. Backend setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate            # Windows PowerShell/cmd
# source venv/bin/activate       # macOS/Linux
pip install -r requirements.txt
pip install openpyxl             # REQUIRED but missing from requirements.txt — see below
copy .env.example .env           # then edit .env
uvicorn app.main:app --reload --port 8000
```

> **Do not skip `pip install openpyxl`.** `requirements.txt` does not list it,
> but the code needs it for: `POST /api/fetch/ecc` (reads `.xlsx`),
> `GET /api/download/...` (writes `.xlsx`), `POST /api/validate/upload` with
> Excel files, and `python -m app.services.mapping_loader --rebuild`. Without it
> ECC fetch returns HTTP 502 with a "Missing optional dependency 'openpyxl'"
> message and downloads return HTTP 500. See
> [known issue #1](08-known-issues.md#1-openpyxl-is-missing-from-requirementstxt).

Verify it is up:

```bash
curl http://localhost:8000/api/health        # → {"status":"ok"}
```

Interactive OpenAPI docs are served by FastAPI at
`http://localhost:8000/docs` (Swagger UI) and `/redoc`.

The backend **must be started from the `backend/` directory**: imports are
absolute (`from app…`), and `SP_OUTPUT_DIR` defaults to the *relative* path
`./data/combined`, which resolves against the current working directory.
Starting Uvicorn from anywhere else silently writes/reads a different
`data/combined` folder.

## 3. Configuration reference (`backend/.env`)

Loaded by `python-dotenv` in `app/core/config.py` at import time. The `.env` file
is git-ignored; `.env.example` is the committed template.

| Variable | Default | In `.env.example`? | Purpose |
|---|---|---|---|
| `SP_TENANT_ID` | `""` | yes (blank) | Azure AD tenant (directory) ID. Used to build the authority URL `https://login.microsoftonline.com/{tenant}`. |
| `SP_CLIENT_ID` | `""` | yes (blank) | Application (client) ID of the app registration ("SharePoint File Fetcher" per the code comment). |
| `SP_CLIENT_SECRET` | `""` | yes (blank) | Client secret for that app. **Secret** — never commit. |
| `SP_HOSTNAME` | `adam702.sharepoint.com` | yes | SharePoint tenant hostname used to resolve the site. |
| `SP_SITE_PATH` | `/sites/ROSharePointAutomation` | yes | Server-relative site path. Combined with the hostname: `GET /sites/{host}:{path}`. |
| `SP_FOLDER_PATH` | `Databricks Files` | yes | Folder (inside the site's default document library) that holds the **S4** CSV extracts. |
| `SP_ECC_FOLDER_PATH` | `""` (empty = library root) | **no** | Folder holding the **ECC** `.xlsx` extracts. Empty string means "children of `/drive/root`". *Missing from `.env.example`* — add it if ECC files ever move into a sub-folder. |
| `SP_OUTPUT_DIR` | `./data/combined` | yes | Where combined CSVs are written and read. Relative paths resolve against the process's working directory. |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | yes | The single allowed CORS origin. |

If `SP_TENANT_ID`/`SP_CLIENT_ID`/`SP_CLIENT_SECRET` are empty, the fetch
endpoints return HTTP 502 with an MSAL error message (see troubleshooting).

### 3.1 Azure AD app registration requirements

The code uses MSAL's **client-credentials** flow
(`acquire_token_for_client`, scope `https://graph.microsoft.com/.default`).
That is an *app-only* flow, so the app registration needs **Application**
(not Delegated) Microsoft Graph permissions with admin consent, sufficient to
read the SharePoint site's document library. The repo does not record which
permission is configured; typical choices are `Sites.Read.All`, `Files.Read.All`,
or `Sites.Selected` with a read grant on the `ROSharePointAutomation` site.
*(This is an inference from the Graph calls the code makes, not something the
repo states.)* The app only ever **reads**; it never uploads to SharePoint.

Graph calls made (all `GET`):

| Purpose | URL |
|---|---|
| Resolve site | `https://graph.microsoft.com/v1.0/sites/{host}:{sitePath}` |
| List a sub-folder | `…/sites/{siteId}/drive/root:/{folderPath}:/children` |
| List library root | `…/sites/{siteId}/drive/root/children` |
| Download a file | `…/sites/{siteId}/drive/items/{itemId}/content` (Graph replies with a redirect; `requests` follows it) |

## 4. Frontend setup

```bash
cd frontend
npm install
npm run dev          # Vite dev server on http://localhost:5173
```

| Script | Command | Notes |
|---|---|---|
| `npm run dev` | `vite` | Dev server, port 5173 (set in `vite.config.js`; not `strictPort`, so Vite picks another port if 5173 is busy — then CORS will block requests). |
| `npm run build` | `vite build` | Production bundle in `frontend/dist/` (git-ignored). |
| `npm run preview` | `vite preview` | Serves the built bundle locally. |

There is no lint script, formatter config or test runner.

**Pointing the UI at a different backend:** every API module reads
`import.meta.env.VITE_API_BASE_URL` and falls back to `http://localhost:8000`.
Create `frontend/.env.local`:

```
VITE_API_BASE_URL=https://migration-api.example.com
```

and remember to set the backend's `FRONTEND_ORIGIN` to the UI's origin.

The app opens on `/` which redirects to `/data-fetch`.

## 5. Typical operating procedure

1. **Start both servers** (§2, §4).
2. Open **Data Fetch**.
   * Click **Fetch ECC** → pulls `MARC_DAP*` / `MBEW_DAP*` Excel files from the
     library root, writes `MARC/ECC` and `MBEW/ECC` combined CSVs.
   * Click **Fetch S4** → pulls `S_MARC#FreeText*` / `S_MBEW#FreeText*` CSVs from
     `Databricks Files`, writes `MARC/S4` and `MBEW/S4` combined CSVs.
   * On each resulting card: **Preview file** shows the first 20 rows;
     **Download .xlsx** converts and downloads the whole combined file.
3. Open **Validation**, pick **MARC** or **MBEW**, leave **Latest files** selected
   (or choose **Upload files** and supply an ECC file and an S/4 file), and click
   **Run Validation**.
4. Read the summary card (overall status, row counts, source filenames) and expand
   each rule card's *Show details*.

> **Read [known issue #2](08-known-issues.md#2-validation-cannot-match-the-committed-data-header-and-value-mismatch)
> before trusting a validation result.** With the currently committed data the
> validator reports 146/146 (MARC) and 41/41 (MBEW) mapped fields "missing".

Fetch order does not matter, and each fetch is independent. Each fetch
**overwrites** the previous combined files; there is no history.

## 6. Where files end up

```
backend/data/combined/
├── MARC/
│   ├── ECC/MARC_ECC_combined.csv
│   └── S4/MARC_S4_combined.csv
└── MBEW/
    ├── ECC/MBEW_ECC_combined.csv
    └── S4/MBEW_S4_combined.csv
```

Directories are created on demand (`os.makedirs(..., exist_ok=True)`). CSVs are
written with `index=False` and pandas' default UTF-8 encoding. The XLSX download
is generated per request and never stored.

## 7. Maintenance tasks

### 7.1 Refresh the mapping

The workbook `backend/app/data/MARC_MBEW_Mappings.xlsx` is the source of truth;
the app reads only the pre-parsed JSON. After editing the workbook:

```bash
cd backend
pip install openpyxl                       # if not installed
python -m app.services.mapping_loader --rebuild
```

This rewrites `backend/app/data/marc_mbew_mapping.json`. Restart the backend —
the loader uses `functools.lru_cache`, so a running server keeps the old mapping
until it restarts (`--reload` restarts on file changes to `.py` files by default,
so also touch a `.py` file or restart manually). Commit both files together.
Expected workbook layout is documented in
[07-data-and-mapping.md](07-data-and-mapping.md#2-the-mapping-workbook-and-json).

### 7.2 Add another file family (e.g. `MLAN`)

*Fetching* needs one line in each of two dicts in
`backend/app/services/merge_service.py`:

```python
S4_GROUPS  = {"MARC": "S_MARC#FreeText", "MBEW": "S_MBEW#FreeText", "MLAN": "S_MLAN#FreeText"}
ECC_GROUPS = {"MARC": "MARC_DAP",        "MBEW": "MBEW_DAP",        "MLAN": "MLAN_DAP"}
```

`get_preview()` validates the family against `S4_GROUPS`, so it becomes
previewable automatically. To make it **validatable** you additionally need to:
add a sheet to the mapping workbook and rebuild the JSON; add the family to
`_SECONDARY_KEY_FIELD` in `mapping_loader.py`; extend `SheetName = Literal[…]` in
`routes/validate.py`; and add it to `SHEETS` in `ValidationActionCard.jsx`.
(The original README mentions only the first step.)

### 7.3 Rotate the SharePoint secret

Edit `SP_CLIENT_SECRET` in `backend/.env` and restart the backend. Nothing is
cached across restarts.

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Fetch returns **502** with `Failed to acquire token: invalid_client …` (or `AADSTS7000215`) | Wrong/expired `SP_CLIENT_SECRET`, or wrong client/tenant ID. | Check the three `SP_*` credentials in `.env`. *(Auth failures surface as 502, not 401 — see known issues.)* |
| 502 with `Your given address (https://login.microsoftonline.com/) should consist of an https url with hostname and a minimum of one segment in a path…` | Empty `SP_TENANT_ID` (the `.env` file is missing or not in `backend/`). *(Message observed with a current MSAL release when running with no credentials; wording may differ on the pinned 1.31.0.)* | Create `backend/.env` from `.env.example` and fill it in. (`load_dotenv()` searches upward from `app/core/`, so it finds `backend/.env` regardless of the working directory — only `SP_OUTPUT_DIR` is cwd-sensitive.) |
| 502 with `403 Client Error: Forbidden` for a Graph URL | App registration lacks (or hasn't had admin consent for) read permission on the site. | Grant an application permission that covers the site and consent. |
| 502 with `404 Client Error: Not Found` on `…/drive/root:/Databricks Files:/children` | `SP_FOLDER_PATH` doesn't exist in the site's default library, or `SP_SITE_PATH`/`SP_HOSTNAME` is wrong. | Confirm the folder name (case/spacing) and site path. |
| ECC fetch returns 502 mentioning `openpyxl` | Package not installed. | `pip install openpyxl`. |
| Fetch "succeeds" but a card shows **0 source files, 0 rows** | No filenames matched the prefix/extension rules (`S_MARC#FreeText…csv`, `MARC_DAP….xlsx/.xls`; matching is **case-sensitive on the prefix**). | Compare the real filenames with the prefixes in `merge_service.py`. **Warning:** this also *overwrites the previous good combined file with an empty one* (known issue #4). |
| Preview returns **404** "hasn't been created yet" | No fetch has been run for that family/source. | Run the fetch. |
| Preview returns **500** `No columns to parse from file` | The combined CSV is empty (an earlier fetch matched no files). | Fix the SharePoint filenames and re-fetch. |
| Validation returns **404** "Directory not found" / "No file matching sheet…" | The combined file doesn't exist under `SP_OUTPUT_DIR` for that sheet/side. | Fetch both ECC and S4 first, and confirm the backend's working directory. |
| Browser console: **CORS error** | UI served from a different origin/port than `FRONTEND_ORIGIN`. | Align `FRONTEND_ORIGIN`, or use port 5173. |
| Validation says **every field is missing** | Header-name mismatch between the mapping and the data files. | See [known issue #2](08-known-issues.md#2-validation-cannot-match-the-committed-data-header-and-value-mismatch). |
| Validation returns **500** `operands could not be broadcast together` | Duplicate business keys in one of the files reached Rule 6. | Known issue #3. |
| `pip install` fails compiling pandas | Python too new for pandas 2.2.2. | Use Python 3.11/3.12. |

## 9. Security notes for operators

* The API has **no authentication or authorization**. Anyone who can reach port
  8000 can trigger fetches (which spend Graph quota), read previews of business
  data, and download full extracts. Do not expose it beyond a trusted network
  without adding auth.
* The client secret lives only in `backend/.env` (git-ignored). The original
  README advises rotating any secret ever pasted in plaintext elsewhere.
* Combined CSVs and the two zip snapshots are **committed to git** and contain
  real extract data. Treat the repository as sensitive.
* Error details from Graph/Python are returned verbatim to the browser.
