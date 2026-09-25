from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import FetchResponse, PreviewResponse
from app.services.merge_service import CombinedFileNotFound, fetch_and_combine, get_preview
from app.services.sharepoint_service import SharePointAuthError

router = APIRouter(prefix="/api", tags=["fetch"])


@router.post("/fetch", response_model=FetchResponse)
def fetch_from_sharepoint() -> FetchResponse:
    """
    Pulls every S_MARC#FreeText / S_MBEW#FreeText file from the
    SharePoint 'Databricks Files' folder, combines each family into one
    CSV, saves it locally, and reports back what happened.
    """
    try:
        result = fetch_and_combine()
    except SharePointAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as-is
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return FetchResponse(
        total_files_fetched=result["total_files_fetched"],
        combined_files=result["combined_files"],
        message="Fetch and merge completed successfully.",
    )


@router.get("/preview/{name}", response_model=PreviewResponse)
def preview_combined_file(
    name: str, limit: int = Query(default=20, ge=1, le=200)
) -> PreviewResponse:
    """
    Returns the first `limit` rows of an already-combined file
    (e.g. name="MARC_combined") so the UI can show a quick glimpse
    without downloading the whole CSV.
    """
    try:
        result = get_preview(name, limit=limit)
    except CombinedFileNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return PreviewResponse(**result)
