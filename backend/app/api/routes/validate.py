import os
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.core.config import settings
from app.models.schemas import RuleResultResponse, ValidationReportResponse
from app.services import validation_service
from app.services.table_io import TableFileNotFound, find_latest, read_table

router = APIRouter(prefix="/api/validate", tags=["validate"])

SheetName = Literal["MARC", "MBEW"]


def _to_response(report, ecc_source: str, s4_source: str) -> ValidationReportResponse:
    return ValidationReportResponse(
        sheet=report.sheet,
        key_fields=report.key_fields,
        ecc_row_count=report.ecc_row_count,
        s4_row_count=report.s4_row_count,
        ecc_source=ecc_source,
        s4_source=s4_source,
        overall_status=report.overall_status.value,
        rules=[
            RuleResultResponse(
                rule_id=r.rule_id,
                name=r.name,
                status=r.status.value,
                summary=r.summary,
                details=r.details,
            )
            for r in report.rules
        ],
    )


@router.get("/latest", response_model=ValidationReportResponse)
def validate_latest(sheet: SheetName = Query(...)) -> ValidationReportResponse:
    """
    For the given sheet (MARC|MBEW), picks:
      - latest combined file in {OUTPUT_DIR}/{sheet}/ECC/
      - latest combined file in {OUTPUT_DIR}/{sheet}/S4/
    and runs all mapping-derived validation rules against them.
    """
    ecc_dir = os.path.join(settings.OUTPUT_DIR, sheet, "ECC")
    s4_dir  = os.path.join(settings.OUTPUT_DIR, sheet, "S4")

    try:
        ecc_path = find_latest(ecc_dir, f"{sheet}_ECC_combined")
        s4_path  = find_latest(s4_dir,  f"{sheet}_S4_combined")
    except TableFileNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    ecc_df = read_table(ecc_path)
    s4_df  = read_table(s4_path)

    report = validation_service.validate(sheet, ecc_df, s4_df)
    return _to_response(report, os.path.basename(ecc_path), os.path.basename(s4_path))


@router.post("/upload", response_model=ValidationReportResponse)
def validate_upload(
    sheet: SheetName = Query(...),
    ecc_file: UploadFile = File(...),
    s4_file: UploadFile = File(...),
) -> ValidationReportResponse:
    """Ad-hoc upload path — unchanged."""
    import io

    def _read_upload(upload: UploadFile):
        import pandas as pd
        ext = os.path.splitext(upload.filename or "")[1].lower()
        content = upload.file.read()
        if ext in (".xlsx", ".xls"):
            return pd.read_excel(io.BytesIO(content), dtype=str)
        return pd.read_csv(io.BytesIO(content), dtype=str)

    try:
        ecc_df = _read_upload(ecc_file)
        s4_df  = _read_upload(s4_file)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read uploaded file(s): {exc}") from exc

    report = validation_service.validate(sheet, ecc_df, s4_df)
    return _to_response(report, ecc_file.filename or "uploaded ECC file", s4_file.filename or "uploaded S4 file")