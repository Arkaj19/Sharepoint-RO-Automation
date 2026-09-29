"""
Pydantic response/request models shared between API and services.
"""
from typing import List, Any, Literal
from pydantic import BaseModel


class CombinedFileStat(BaseModel):
    name: str            # e.g. "MARC_combined"
    family: str          # ← ADD (e.g. "MARC", "MBEW")
    source_type: str     # ← ADD (e.g. "S4", "ECC")
    source_files: int    # how many SharePoint files fed into this one
    row_count: int        # total rows in the combined file
    saved_path: str      # local path where the combined file was written


class FetchResponse(BaseModel):
    total_files_fetched: int
    combined_files: List[CombinedFileStat]
    message: str


class ErrorResponse(BaseModel):
    detail: str


class PreviewResponse(BaseModel):
    name: str
    columns: List[str]
    rows: List[dict]
    total_rows: int
    preview_row_count: int

# -- validation --------------------------------------------------------------

RuleStatusLiteral = Literal["pass", "fail", "warning"]


class RuleResultResponse(BaseModel):
    rule_id: str
    name: str
    status: RuleStatusLiteral
    summary: str
    details: List[Any] = []


class ValidationReportResponse(BaseModel):
    sheet: str
    key_fields: List[str]
    ecc_row_count: int
    s4_row_count: int
    ecc_source: str
    s4_source: str
    overall_status: RuleStatusLiteral
    rules: List[RuleResultResponse]