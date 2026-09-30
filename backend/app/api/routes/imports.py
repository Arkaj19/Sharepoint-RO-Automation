from fastapi import APIRouter, HTTPException

from app.core import paths
from app.core.jsonio import read_json
from app.importers import service
from app.ingest.sources import OBJECTS
from app.mapping.repository import MappingNotFound
from app.store.snapshot_store import SnapshotNotFound

router = APIRouter(prefix="/api/import", tags=["import"])


@router.post("/{obj}")
def import_logic(obj: str) -> dict:
    """Imports the Databricks views (backend/reference_logic/databricks) and the
    business rule workbook as a proposal for review. Takes ~10-30 s: every
    imported rule is scored against the current S/4 output."""
    if obj not in OBJECTS:
        raise HTTPException(status_code=404, detail=f"Unknown object '{obj}'.")
    try:
        rep = service.import_object(obj)
    except (MappingNotFound, SnapshotNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {k: rep.get(k) for k in ("object", "proposal_id", "drift", "dictionary_missing", "untranslated",
                                    "blocks")} | {"main": {k: v for k, v in (rep.get("main") or {}).items()
                                                           if k != "rows"} | {"rows": _rows(rep.get("main"))},
                                                  "variant": {"rows": _rows(rep.get("variant"))}}


def _rows(section: dict | None) -> dict | None:
    rows = (section or {}).get("rows")
    return {k: v for k, v in rows.items() if "sample" not in k} if rows else None


@router.get("")
def list_imports(object: str | None = None, limit: int = 20) -> list[dict]:
    d = paths.imports_dir()
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("IMPORT-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        r = read_json(p) or {}
        if object and r.get("object") != object:
            continue
        out.append({"file": p.name, "object": r.get("object"), "created_at": r.get("created_at"),
                    "proposal_id": r.get("proposal_id"), "drift": r.get("drift"),
                    "summary": (r.get("main") or {}).get("summary"), "rows": _rows(r.get("main")),
                    "variant_rows": _rows(r.get("variant"))})
        if len(out) >= limit:
            break
    return out
