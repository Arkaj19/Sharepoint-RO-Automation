"""
Every filesystem location the app uses, in one place. Paths resolve relative
to the backend/ folder, so the app behaves the same whatever the cwd is.
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[2]

DATA_ROOT = Path(settings.DATA_ROOT) if settings.DATA_ROOT else BACKEND_DIR / "data"
MAPPINGS_DIR = Path(settings.MAPPINGS_DIR) if settings.MAPPINGS_DIR else BACKEND_DIR / "mappings"


def snapshots_dir() -> Path:
    return DATA_ROOT / "snapshots"


def archive_dir() -> Path:
    return DATA_ROOT / "archive"


def changesets_dir() -> Path:
    return DATA_ROOT / "changesets"


def proposals_dir() -> Path:
    return DATA_ROOT / "proposals"


def agent_runs_dir() -> Path:
    return DATA_ROOT / "agent_runs"


def rulebooks_dir() -> Path:
    return DATA_ROOT / "rulebooks"


def refresh_runs_dir() -> Path:
    return DATA_ROOT / "refresh_runs"


def incoming_dir() -> Path:
    """Local drop folder for supporting tables not on SharePoint."""
    return DATA_ROOT / "incoming"


def outputs_dir() -> Path:
    return DATA_ROOT / "outputs"


def imports_dir() -> Path:
    return DATA_ROOT / "imports"


def refresh_lock_path() -> Path:
    return DATA_ROOT / ".refresh.lock"


def legacy_combined_dir() -> Path:
    out = Path(settings.OUTPUT_DIR)
    return out if out.is_absolute() else (BACKEND_DIR / out).resolve()
