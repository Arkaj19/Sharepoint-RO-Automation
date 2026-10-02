from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import PreviewResponse
from app.services.merge_service import (
    CombinedFileNotFound,
    get_preview,
)
from app.services.transformation_preview_service import transformation_service
from app.services.ecc_transformation_service import ReferenceDataMissing


router = APIRouter(
    prefix="/api/preview",
    tags=["preview"],
)


# ------------------------------------------------------------------
# Transformation preview
# ------------------------------------------------------------------

@router.get("/transformation/{table_type}")
def get_transformation(
    table_type: Literal["MARC"],
    limit: int = Query(20, ge=1, le=100),
    matnr: list[str] = Query(default=[]),
):
    try:
        return transformation_service.get_transformation_preview(
            table_type,
            matnr,
            limit,
        )

    except ReferenceDataMissing as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        )

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )


# ------------------------------------------------------------------
# Combined file preview
# ------------------------------------------------------------------

@router.get(
    "/{family}/{source_type}",
    response_model=PreviewResponse,
)
def preview_combined_file(
    family: str,
    source_type: str,
    limit: int = Query(default=20, ge=1, le=200),
):
    try:
        result = get_preview(
            family,
            source_type,
            limit=limit,
        )

    except CombinedFileNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    return PreviewResponse(**result)