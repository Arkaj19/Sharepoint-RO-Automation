from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.generation import service
from app.ingest.sources import OBJECTS
from app.mapping.repository import MappingNotFound
from app.store.snapshot_store import SnapshotNotFound

router = APIRouter(prefix="/api/outputs", tags=["outputs"])


@router.post("/{obj}/generate")
def generate(obj: str) -> dict:
    """Generates S_<OBJECT> from the current ECC data and the approved mapping,
    and compares it with the current S/4 (Databricks) extract."""
    if obj not in OBJECTS:
        raise HTTPException(status_code=404, detail=f"Unknown object '{obj}'.")
    try:
        return service.run(obj)
    except (MappingNotFound, SnapshotNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
def list_outputs(object: str | None = None, limit: int = 20) -> list[dict]:
    return service.list_runs(object, limit)


@router.get("/{obj}/{run_id}")
def get_output(obj: str, run_id: str) -> dict:
    rep = service.get_run(obj, run_id)
    if rep is None:
        raise HTTPException(status_code=404, detail=f"No output run '{run_id}'.")
    return rep


@router.get("/{obj}/{run_id}/download")
def download_output(obj: str, run_id: str):
    path = service.output_path(obj, run_id)
    if "/" in run_id or "\\" in run_id or not path.exists():
        raise HTTPException(status_code=404, detail="No generated file for this run.")
    return FileResponse(path, media_type="text/csv", filename=f"S_{obj}_{run_id}.csv")
