"""
Response models for the validation routes.
"""
from typing import Any, List, Literal, Optional

from pydantic import BaseModel

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
    mapping_version: Optional[int] = None
    notes: List[str] = []
