"""
Request/response models for refresh and snapshot routes.
"""
from typing import List, Optional

from pydantic import BaseModel


class RefreshRequest(BaseModel):
    force: bool = False
    objects: Optional[List[str]] = None
    run_agent: bool = True


class PreviewResponse(BaseModel):
    name: str
    version_id: Optional[str] = None
    columns: List[str]
    rows: List[dict]
    total_rows: int
    preview_row_count: int
