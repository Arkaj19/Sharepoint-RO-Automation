import logging
import os
from typing import Literal, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.core.config import settings
from app.models.schemas import RuleResultResponse, ValidationReportResponse
from app.services import validation_service
from app.services.ecc_transformation_service import ReferenceDataMissing, build_expected_s4
from app.services.table_io import TableFileNotFound, find_latest, read_table

router = APIRouter(prefix="/api/validate", tags=["validate"])
log = logging.getLogger(__name__)

SheetName = Literal["MARC", "MBEW"]


def _to_response(report, ecc_source: str, s4_source: str) -> ValidationReportResponse:
    return ValidationReportResponse(
        sheet=report.sheet,
        key_fields=report.key_fields,
        ecc_row_count=report.ecc_row_count,
        s4_row_count=report.s4_row_count,
        records_validated=report.records_validated,
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


def _scope_to_s_mara(actual_df):
    """POC scope filter: keep only Actual S/4 rows whose product is listed in the S_MARA
    reference table. Expected S/4 is already restricted by S_MARA inside the transformation,
    so both sides then cover the same set of materials. Turn off with SP_SCOPE_TO_S_MARA=0."""
    if os.getenv("SP_SCOPE_TO_S_MARA", "1") == "0":
        return actual_df
    try:
        path = find_latest(os.path.join(settings.OUTPUT_DIR, "S_MARA", "ECC"), "S_MARA_ECC_combined")
    except TableFileNotFound:
        return actual_df
    sm = read_table(path)
    src = next((c for c in ("PRODUCT", "Product", "Material", "MATNR") if c in sm.columns), None)
    dst = next((c for c in ("PRODUCT", "MATNR", "Product", "Material") if c in actual_df.columns), None)
    if src is None or dst is None:
        return actual_df
    keep = set(sm[src].dropna().astype(str).str.lstrip("0"))
    mask = actual_df[dst].astype(str).str.lstrip("0").isin(keep)
    log.info("Scoped Actual S/4 to S_MARA products: %d of %d rows kept", int(mask.sum()), len(actual_df))
    return actual_df[mask].reset_index(drop=True)


def _load_frames(sheet: str):
    ecc_dir = os.path.join(settings.OUTPUT_DIR, sheet, "ECC")
    s4_dir  = os.path.join(settings.OUTPUT_DIR, sheet, "S4")
    try:
        ecc_path = find_latest(ecc_dir, f"{sheet}_ECC_combined")
        s4_path  = find_latest(s4_dir,  f"{sheet}_S4_combined")
    except TableFileNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raw_ecc_df = read_table(ecc_path)
    actual_s4_df = read_table(s4_path)
    if sheet != "MARC":  # MARC follows the rule book only (no S_MARA scoping); MBEW is unchanged
        actual_s4_df = _scope_to_s_mara(actual_s4_df)
    try:
        expected_s4_df = build_expected_s4(sheet, raw_ecc_df)
    except ReferenceDataMissing as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as-is
        raise HTTPException(status_code=500, detail=f"ECC -> S/4 transformation failed: {exc}") from exc
    return raw_ecc_df, expected_s4_df, actual_s4_df, ecc_path, s4_path


@router.get("/latest", response_model=ValidationReportResponse)
def validate_latest(
    sheet: SheetName = Query(...),
    plant: Optional[str] = Query(None, description="Restrict to one S/4 plant (MARC) / valuation area (MBEW), e.g. US30"),
) -> ValidationReportResponse:
    """
    Validates the ECC -> S/4 *transformation* for the given sheet (MARC|MBEW):
    raw ECC -> transformation = Expected S/4, compared with Actual S/4 (Databricks)
    using the mapping-derived rules.

    With ?plant=XXXX the transformation still runs on all ECC rows (the derived plant
    depends on them) and only the Expected/Actual frames are filtered afterwards.
    `ecc_row_count` is always the total raw ECC rows read.
    """
    raw_ecc_df, expected_s4_df, actual_s4_df, ecc_path, s4_path = _load_frames(sheet)
    if plant:
        report = validation_service.validate_plant(
            sheet, expected_s4_df, actual_s4_df, plant, ecc_row_count=len(raw_ecc_df))
    else:
        report = validation_service.validate_expected_vs_actual(
            sheet, expected_s4_df, actual_s4_df, ecc_row_count=len(raw_ecc_df))
    return _to_response(report, os.path.basename(ecc_path), os.path.basename(s4_path))


@router.get("/by-plant")
def validate_by_plant(sheet: SheetName = Query(...)) -> dict:
    """One summary row per S/4 plant: rule statuses, row counts, missing/unexpected keys,
    number of fields with mismatches. Use ?plant=XXXX on /latest for the full report."""
    _, expected_s4_df, actual_s4_df, ecc_path, s4_path = _load_frames(sheet)
    return {
        "sheet": sheet,
        "ecc_source": os.path.basename(ecc_path),
        "s4_source": os.path.basename(s4_path),
        "plants": validation_service.validate_by_plant(sheet, expected_s4_df, actual_s4_df),
    }


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