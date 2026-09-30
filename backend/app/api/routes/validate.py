from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.ingest.readers import read_upload
from app.mapping.repository import MappingNotFound
from app.schemas.validation import RuleResultResponse, ValidationReportResponse
from app.store import snapshot_store as ss
from app.validation import service as validation_service

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
        mapping_version=report.mapping_version,
        notes=report.notes,
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


def _run(sheet: str, ecc_df, s4_df):
    try:
        return validation_service.validate(sheet, ecc_df, s4_df)
    except MappingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/latest", response_model=ValidationReportResponse)
def validate_latest(sheet: SheetName = Query(...)) -> ValidationReportResponse:
    """Validates the current ECC and S/4 snapshot versions of `sheet` against
    its YAML mapping."""
    try:
        ecc_df = ss.read_combined(sheet, "ECC")
        s4_df = ss.read_combined(sheet, "S4")
    except ss.SnapshotNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    report = _run(sheet, ecc_df, s4_df)
    return _to_response(report, f"{sheet}/ECC {ss.resolve_version(sheet, 'ECC')}",
                        f"{sheet}/S4 {ss.resolve_version(sheet, 'S4')}")


@router.post("/upload", response_model=ValidationReportResponse)
def validate_upload(
    sheet: SheetName = Query(...),
    ecc_file: UploadFile = File(...),
    s4_file: UploadFile = File(...),
) -> ValidationReportResponse:
    """Ad-hoc validation of two uploaded files (CSV or .xlsx)."""
    try:
        ecc_df = read_upload(ecc_file.filename, ecc_file.file.read())
        s4_df = read_upload(s4_file.filename, s4_file.file.read())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read uploaded file(s): {exc}") from exc

    report = _run(sheet, ecc_df, s4_df)
    return _to_response(report, ecc_file.filename or "uploaded ECC file", s4_file.filename or "uploaded S4 file")
