from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.schemas.fetch import RefreshRequest
from app.workflows import refresh

router = APIRouter(prefix="/api/refresh", tags=["refresh"])


@router.post("", status_code=202)
def start_refresh(body: RefreshRequest, background: BackgroundTasks) -> dict:
    """Starts 'Refresh data' in the background; poll GET /api/refresh/{id}."""
    rec = refresh.create(force=body.force, objects=body.objects, run_agent=body.run_agent)
    background.add_task(refresh.execute, rec["refresh_run_id"])
    return rec


@router.get("")
def list_refresh_runs(limit: int = 20) -> list[dict]:
    return refresh.list_runs(limit)


@router.get("/{run_id}")
def get_refresh_run(run_id: str) -> dict:
    rec = refresh.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No refresh run '{run_id}'.")
    return rec
