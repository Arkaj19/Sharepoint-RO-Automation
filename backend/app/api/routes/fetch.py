from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from app.models.schemas import FetchResponse, PreviewResponse
from app.services.merge_service import (
    CombinedFileNotFound, build_xlsx_bytes, fetch_s4_files, fetch_ecc_files, get_preview
)
from app.services.sharepoint_service import SharePointAuthError

router = APIRouter(prefix="/api", tags=["fetch"])

@router.post("/fetch/s4", response_model=FetchResponse)
def fetch_s4():
    try:
        result = fetch_s4_files()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return FetchResponse(
        total_files_fetched=result["total_files_fetched"],
        combined_files=result["combined_files"],
        message="S4 Databricks files fetched and merged."
    )

@router.post("/fetch/ecc", response_model=FetchResponse)
def fetch_ecc():
    try:
        result = fetch_ecc_files()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return FetchResponse(
        total_files_fetched=result["total_files_fetched"],
        combined_files=result["combined_files"],
        message="ECC DAP files fetched and merged."
    )

@router.get("/preview/{family}/{source_type}", response_model=PreviewResponse)
def preview_combined_file(family: str, source_type: str, limit: int = Query(default=20, ge=1, le=200)):
    try:
        result = get_preview(family, source_type, limit=limit)
    except CombinedFileNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return PreviewResponse(**result)

@router.get("/download/{family}/{source_type}")
def download_combined_file(family: str, source_type: str):
    try:
        buffer = build_xlsx_bytes(family, source_type)
    except CombinedFileNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    
    filename = f"{family}_{source_type}_combined.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )