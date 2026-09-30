"""
Request models for proposal review and agent / rule book actions.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel


class OpDecision(BaseModel):
    op_id: str
    decision: Literal["accepted", "rejected", "pending"]
    comment: Optional[str] = None


class DecisionsRequest(BaseModel):
    decisions: List[OpDecision]
    decided_by: Optional[str] = None


class ApplyRequest(BaseModel):
    applied_by: Optional[str] = None


class AgentRunRequest(BaseModel):
    object: str


class RuleBookRequest(BaseModel):
    objects: Optional[List[str]] = None
    include_drafts: bool = False
