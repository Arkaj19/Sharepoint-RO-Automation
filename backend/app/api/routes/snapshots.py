from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.ingest.sources import OBJECTS, REF, REF_TABLES, SIDES, get_source
from app.schemas.fetch import PreviewResponse
from app.store import preview, snapshot_store as ss
from app.workflows import refresh

router = APIRouter(prefix="/api", tags=["snapshots"])

Side = str
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _check_object(obj: str) -> None:
    if obj not in OBJECTS and obj != REF:
        raise HTTPException(status_code=404, detail=f"Unknown object '{obj}'. Known: {OBJECTS}")


@router.get("/snapshots")
def list_snapshots() -> list[dict]:
    """Current + previous versions per dataset (objects and supporting tables), and archived version ids."""
    out = ss.list_datasets(OBJECTS, SIDES)
    for d in ss.list_datasets([REF], tuple(REF_TABLES)):
        spec = get_source(REF, d["side"])
        out.append({**d, "supporting": True, "required": spec.required, "description": spec.description})
    return out


@router.get("/snapshots/{obj}/{side}/{version}/manifest")
def get_manifest(obj: str, side: Side, version: str) -> dict:
    _check_object(obj)
    try:
        return ss.manifest(obj, side, version)
    except ss.SnapshotNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/snapshots/{obj}/{side}/{version}/preview", response_model=PreviewResponse)
def preview_version(obj: str, side: Side, version: str, limit: int = Query(default=20, ge=1, le=200)):
    _check_object(obj)
    try:
        return preview.get_preview(obj, side, version, limit)
    except ss.SnapshotNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/snapshots/{obj}/{side}/{version}/download")
def download_version(obj: str, side: Side, version: str):
    _check_object(obj)
    try:
        buffer, filename = preview.build_xlsx_bytes(obj, side, version)
    except ss.SnapshotNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StreamingResponse(buffer, media_type=XLSX,
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# -- deprecated aliases (old Data Fetch routes), kept for one release --------------------

@router.get("/preview/{family}/{source_type}", response_model=PreviewResponse, deprecated=True)
def legacy_preview(family: str, source_type: Side, limit: int = Query(default=20, ge=1, le=200)):
    return preview_version(family, source_type, "current", limit)


@router.get("/download/{family}/{source_type}", deprecated=True)
def legacy_download(family: str, source_type: Side):
    return download_version(family, source_type, "current")


@router.post("/fetch/{source_type}", status_code=202, deprecated=True)
def legacy_fetch(source_type: str, background: BackgroundTasks) -> dict:
    """Old per-side fetch. Now starts a full refresh (both sides, all objects)."""
    rec = refresh.create()
    background.add_task(refresh.execute, rec["refresh_run_id"])
    return {**rec, "message": "Deprecated: use POST /api/refresh. A full refresh was started."}
