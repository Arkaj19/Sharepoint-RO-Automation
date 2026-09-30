from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.agents.runtime import run_log
from app.ingest.sources import OBJECTS
from app.schemas.proposals import AgentRunRequest
from app.workflows import refresh

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/run", status_code=202)
def rerun_agent(body: AgentRunRequest, background: BackgroundTasks) -> dict:
    """Re-runs Agent 1 for one object against the current data and mapping,
    without a refresh. Watch GET /api/agent/runs?object=... for the result."""
    if body.object not in OBJECTS:
        raise HTTPException(status_code=404, detail=f"Unknown object '{body.object}'.")
    background.add_task(refresh.rerun_agent, body.object)
    return {"status": "started", "object": body.object}


@router.get("/runs")
def list_runs(object: str | None = None, limit: int = 20) -> list[dict]:
    return run_log.list_runs(object, limit)


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    rec = run_log.get_run(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No agent run '{run_id}'.")
    return rec


@router.get("/runs/{run_id}/transcript")
def get_transcript(run_id: str) -> list[dict]:
    if run_log.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"No agent run '{run_id}'.")
    return run_log.get_transcript(run_id)
