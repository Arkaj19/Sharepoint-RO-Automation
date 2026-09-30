from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core import paths
from app.rulebook import writer
from app.schemas.proposals import RuleBookRequest

router = APIRouter(prefix="/api/rulebook", tags=["rulebook"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("")
def generate(body: RuleBookRequest) -> dict:
    """Builds a new rule book .xlsx from the current YAML mappings."""
    out = writer.build(body.objects, include_drafts=body.include_drafts)
    return {"name": out.name, "size": out.stat().st_size}


@router.get("")
def list_rulebooks() -> list[dict]:
    return writer.list_files()


@router.get("/{name}/download")
def download(name: str):
    if name == "latest":
        files = writer.list_files()
        if not files:
            raise HTTPException(status_code=404, detail="No rule book generated yet.")
        name = files[0]["name"]
    path = paths.rulebooks_dir() / name
    if "/" in name or "\\" in name or not name.startswith("RuleBook_") or not path.exists():
        raise HTTPException(status_code=404, detail=f"No rule book '{name}'.")
    return FileResponse(path, media_type=XLSX, filename=name)
