"""
Pydantic response/request models shared between API and services.
"""
from typing import List
from pydantic import BaseModel


class CombinedFileStat(BaseModel):
    name: str            # e.g. "MARC_combined"
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
