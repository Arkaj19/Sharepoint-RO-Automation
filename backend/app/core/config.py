"""
Central app configuration, loaded from environment variables (.env).
Keeps SharePoint / Graph and Azure OpenAI credentials out of source code.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # Azure AD app registration ("SharePoint File Fetcher")
    TENANT_ID: str = os.getenv("SP_TENANT_ID", "")
    CLIENT_ID: str = os.getenv("SP_CLIENT_ID", "")
    CLIENT_SECRET: str = os.getenv("SP_CLIENT_SECRET", "")

    # SharePoint location
    SHAREPOINT_HOSTNAME: str = os.getenv("SP_HOSTNAME", "adam702.sharepoint.com")
    SITE_PATH: str = os.getenv("SP_SITE_PATH", "/sites/ROSharePointAutomation")

    # S4 (Databricks) files live inside this folder
    FOLDER_PATH: str = os.getenv("SP_FOLDER_PATH", "Databricks Files")

    # ECC (DAP) files live at the ROOT of the Document Library.
    # Empty string => "list children of /drive/root"
    ECC_FOLDER_PATH: str = os.getenv("SP_ECC_FOLDER_PATH", "")

    # Where the extracts come from: sharepoint | local. "local" reads a folder
    # that mirrors the library (root files + sub-folders), e.g. for testing.
    SOURCE_MODE: str = os.getenv("SOURCE_MODE", "sharepoint").strip().lower() or "sharepoint"
    LOCAL_SOURCE_ROOT: str = os.getenv("LOCAL_SOURCE_ROOT", "")

    # Legacy location of the combined files written by the old fetch routes.
    # Only read by scripts/import_legacy_combined.py.
    OUTPUT_DIR: str = os.getenv("SP_OUTPUT_DIR", "./data/combined")

    # Local data root (snapshots, archive, proposals, agent runs, rule books).
    # Empty => backend/data, resolved relative to this file (not the cwd).
    DATA_ROOT: str = os.getenv("DATA_ROOT", "")
    # YAML mappings (committed). Empty => backend/mappings
    MAPPINGS_DIR: str = os.getenv("MAPPINGS_DIR", "")

    # Databricks views + SAP labels used by the importer. Empty => backend/reference_logic
    REFERENCE_LOGIC_DIR: str = os.getenv("REFERENCE_LOGIC_DIR", "")

    # How many previous versions of each dataset to keep next to "current"
    # before older ones are zipped into the archive.
    SNAPSHOT_KEEP_PREVIOUS: int = _int("SNAPSHOT_KEEP_PREVIOUS", 2)
    # Keep the raw SharePoint files inside each snapshot version.
    SNAPSHOT_KEEP_RAW: bool = _bool("SNAPSHOT_KEEP_RAW", True)

    # Timeout (seconds) for every Graph request.
    HTTP_TIMEOUT: int = _int("HTTP_TIMEOUT", 120)

    # CORS - the Vite dev server origin
    FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

    # -- Agent 1 / LLM ------------------------------------------------------
    # azure | none  ("none" = deterministic proposer only, no model calls)
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "azure")
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "")
    AZURE_OPENAI_CHAT_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "")
    LLM_REASONING_EFFORT: str = os.getenv("LLM_REASONING_EFFORT", "medium")
    LLM_MAX_STEPS: int = _int("LLM_MAX_STEPS", 25)
    LLM_RUN_TOKEN_BUDGET: int = _int("LLM_RUN_TOKEN_BUDGET", 800_000)
    LLM_MAX_OUTPUT_TOKENS: int = _int("LLM_MAX_OUTPUT_TOKENS", 16_000)
    LLM_MAX_SAMPLE_ROWS: int = _int("LLM_MAX_SAMPLE_ROWS", 50)
    LLM_MAX_SAMPLE_ROWS_PER_RUN: int = _int("LLM_MAX_SAMPLE_ROWS_PER_RUN", 200)
    # Samples go to the model unmasked unless this is switched on.
    LLM_MASKING: bool = _bool("LLM_MASKING", False)


settings = Settings()
