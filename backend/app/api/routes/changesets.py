from fastapi import APIRouter, HTTPException

from app.diff import changeset

router = APIRouter(prefix="/api/changesets", tags=["changesets"])


@router.get("")
def list_changesets(object: str | None = None, limit: int = 20) -> list[dict]:
    return changeset.list_all(object, limit)


@router.get("/{changeset_id}")
def get_changeset(changeset_id: str) -> dict:
    try:
        return changeset.load(changeset_id)
    except changeset.ChangeSetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
