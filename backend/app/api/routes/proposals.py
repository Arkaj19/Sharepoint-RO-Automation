from fastapi import APIRouter, HTTPException

from app.mapping.repository import MappingNotFound
from app.proposals import service
from app.schemas.proposals import ApplyRequest, DecisionsRequest

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


@router.get("")
def list_proposals(object: str | None = None, status: str | None = None, limit: int = 30) -> list[dict]:
    return service.list_all(object, status, limit)


@router.get("/{proposal_id}")
def get_proposal(proposal_id: str) -> dict:
    try:
        return service.get(proposal_id)
    except service.ProposalNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{proposal_id}/decisions")
def decide(proposal_id: str, body: DecisionsRequest) -> dict:
    """Accept / reject (or reset to pending) one or more operations."""
    try:
        return service.decide(proposal_id, [d.model_dump() for d in body.decisions], by=body.decided_by)
    except service.ProposalNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.ProposalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{proposal_id}/apply")
def apply(proposal_id: str, body: ApplyRequest) -> dict:
    """Writes the accepted operations to the YAML mapping (one new version)."""
    try:
        return service.apply(proposal_id, by=body.applied_by)
    except (service.ProposalNotFound, MappingNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.ProposalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
