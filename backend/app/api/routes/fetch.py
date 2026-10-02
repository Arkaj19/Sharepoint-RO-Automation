from fastapi import APIRouter, HTTPException

from app.models.schemas import FetchResponse
from app.services.merge_service import (
    fetch_s4_files,
    fetch_ecc_files,
)

router = APIRouter(
    prefix="/api/fetch",
    tags=["fetch"],
)


@router.post("/s4", response_model=FetchResponse)
def fetch_s4():
    try:
        result = fetch_s4_files()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return FetchResponse(
        total_files_fetched=result["total_files_fetched"],
        combined_files=result["combined_files"],
        message="S4 Databricks files fetched and merged.",
    )


@router.post("/ecc", response_model=FetchResponse)
def fetch_ecc():
    try:
        result = fetch_ecc_files()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return FetchResponse(
        total_files_fetched=result["total_files_fetched"],
        combined_files=result["combined_files"],
        message="ECC DAP files fetched and merged.",
    )