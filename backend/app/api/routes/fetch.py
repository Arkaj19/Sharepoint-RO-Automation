# from fastapi import APIRouter, HTTPException, Query
# from fastapi.responses import StreamingResponse

# from app.models.schemas import FetchResponse, PreviewResponse
# from app.services.merge_service import (
#     CombinedFileNotFound,
#     build_xlsx_bytes,
#     fetch_and_combine,
#     get_preview,
# )
# from app.services.sharepoint_service import SharePointAuthError

# router = APIRouter(prefix="/api", tags=["fetch"])

# @router.get("/health")
# def health_check():
#     return {"status": "ok"}


# @router.post("/fetch", response_model=FetchResponse)
# def fetch_from_sharepoint() -> FetchResponse:
#     """
#     Pulls every S_MARC#FreeText / S_MBEW#FreeText file from the
#     SharePoint 'Databricks Files' folder, combines each family into one
#     CSV, saves it locally, and reports back what happened.
#     """
#     try:
#         result = fetch_and_combine()
#     except SharePointAuthError as exc:
#         raise HTTPException(status_code=401, detail=str(exc)) from exc
#     except Exception as exc:  # noqa: BLE001 - surfaced to the UI as-is
#         raise HTTPException(status_code=502, detail=str(exc)) from exc

#     return FetchResponse(
#         total_files_fetched=result["total_files_fetched"],
#         combined_files=result["combined_files"],
#         message="Fetch and merge completed successfully.",
#     )


# @router.get("/preview/{name}", response_model=PreviewResponse)
# def preview_combined_file(
#     name: str, limit: int = Query(default=20, ge=1, le=200)
# ) -> PreviewResponse:
#     """
#     Returns the first `limit` rows of an already-combined file
#     (e.g. name="MARC_combined") so the UI can show a quick glimpse
#     without downloading the whole CSV.
#     """
#     try:
#         result = get_preview(name, limit=limit)
#     except CombinedFileNotFound as exc:
#         raise HTTPException(status_code=404, detail=str(exc)) from exc
#     except Exception as exc:  # noqa: BLE001
#         raise HTTPException(status_code=500, detail=str(exc)) from exc

#     return PreviewResponse(**result)


# @router.get("/download/{name}")
# def download_combined_file(name: str) -> StreamingResponse:
#     """
#     Converts the stored .csv for `name` to .xlsx on the fly and streams it
#     to the client. The .xlsx is built in memory per-request - it is never
#     cached on disk, so a fetch stays cheap even if nobody ever downloads.
#     """
#     try:
#         buffer = build_xlsx_bytes(name)
#     except CombinedFileNotFound as exc:
#         raise HTTPException(status_code=404, detail=str(exc)) from exc
#     except Exception as exc:  # noqa: BLE001
#         raise HTTPException(status_code=500, detail=str(exc)) from exc

#     return StreamingResponse(
#         buffer,
#         media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
#         headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
#     )

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