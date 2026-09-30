import difflib

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.mapping import render, repository
from app.transforms.context import availability

router = APIRouter(prefix="/api/mappings", tags=["mappings"])


def _load(obj: str):
    try:
        return repository.load(obj)
    except repository.MappingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
def list_mappings() -> list[dict]:
    out = []
    for obj in repository.list_objects():
        doc = repository.load(obj)
        out.append({"object": obj, "title": doc.title, "mapping_version": doc.mapping_version,
                    "updated_at": doc.updated_at, "updated_by": doc.updated_by, "fields": len(doc.fields),
                    "ecc_mapped": sum(1 for f in doc.fields if doc.has_source(f) or doc.is_derived_only(f)),
                    "approved": sum(1 for f in doc.fields if f.provenance.status == "approved"),
                    "hand_edited": repository.is_hand_edited(doc)})
    return out


@router.get("/{obj}")
def get_mapping(obj: str) -> dict:
    doc = _load(obj)
    avail = availability(doc)
    return {
        "object": doc.object, "title": doc.title, "mapping_version": doc.mapping_version,
        "updated_at": doc.updated_at, "updated_by": doc.updated_by, "hand_edited": repository.is_hand_edited(doc),
        "key": doc.key.fields,
        "fields": [{**render.field_summary(f, doc), "group": f.group, "confidence": f.provenance.confidence,
                    "reason": f.provenance.reason, "rule_id": f.provenance.rule_id, "source": f.provenance.source,
                    "version": f.provenance.version, "ecc_source": f.ecc_source.model_dump(exclude_none=True),
                    "logic_type": render.logic(doc, f)[0], "logic": render.logic(doc, f)[1],
                    "conflict": ((f.reference or {}).get("conflict") or {}).get("detail"),
                    "workbook_logic": ((f.reference or {}).get("workbook") or {}).get("logic")}
                   for f in doc.fields],
        "lookups": [{"id": lk.id, "table": lk.table, "text": render.lookup_text(lk), "available": avail.get(lk.table),
                     "description": lk.description} for lk in doc.lookups],
        "dictionary": {t: len(e) for t, e in doc.dictionary.items()},
        "value_maps": {k: {"description": v.description, "on_missing": v.on_missing,
                           "entries": [{"from": e.from_, "to": e.to, "status": e.provenance.status,
                                        "confidence": e.provenance.confidence} for e in v.entries]}
                       for k, v in doc.value_maps.items()},
        "filters": [{"id": x.id, "text": render.filter_text(x), "source_text": x.source_text,
                     "status": x.provenance.status} for x in doc.filters],
        "ignored_columns": doc.ignored_columns.model_dump(),
        "raw_condition_notes": doc.raw_condition_notes,
    }


@router.get("/{obj}/yaml", response_class=PlainTextResponse)
def get_yaml(obj: str, version: int | None = None) -> str:
    _load(obj)
    try:
        return repository.read_text(obj, version)
    except repository.MappingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{obj}/versions")
def list_versions(obj: str) -> list[dict]:
    _load(obj)
    return repository.list_versions(obj)


@router.get("/{obj}/diff", response_class=PlainTextResponse)
def diff_versions(obj: str, from_version: int, to_version: int | None = None) -> str:
    _load(obj)
    try:
        a = repository.read_text(obj, from_version)
        b = repository.read_text(obj, to_version)
    except repository.MappingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return "".join(difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                        fromfile=f"{obj} v{from_version}", tofile=f"{obj} v{to_version or 'current'}"))


@router.get("/{obj}/changelog")
def changelog(obj: str) -> list[dict]:
    _load(obj)
    return list(reversed(repository.read_changelog(obj)))
