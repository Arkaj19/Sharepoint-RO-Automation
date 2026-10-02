from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.services.merge_service import (
    CombinedFileNotFound,
    build_xlsx_bytes,
)


router = APIRouter(
    prefix="/api/download",
    tags=["download"],
)


@router.get("/{family}/{source_type}")
def download_combined_file(
    family: str,
    source_type: str,
):
    try:
        buffer = build_xlsx_bytes(
            family,
            source_type,
        )

    except CombinedFileNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    filename = f"{family}_{source_type}_combined.xlsx"

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )