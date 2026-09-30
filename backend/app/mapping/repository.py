"""
Reads and writes the YAML mappings in backend/mappings/.

  mappings/<OBJECT>.yaml                 current mapping (single source of truth)
  mappings/history/<OBJECT>/v0007.yaml   every previous version, kept on save
  mappings/<OBJECT>.changelog.jsonl      one record per applied operation

Writes go through ruamel.yaml's round-trip mode and are merged into the
existing document, so comments people add by hand survive an app save.
`content_hash` covers the mapping content (not the version/meta fields), so
a hand edit made outside the app is detectable: the stored hash no longer
matches the content.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

from app.core import paths
from app.core.jsonio import append_jsonl, read_jsonl, write_text_atomic
from app.mapping.model import MappingDoc


class MappingNotFound(FileNotFoundError):
    pass


_META_KEYS = {"content_hash", "mapping_version", "updated_at", "updated_by"}
# Always written, even when null, so an unmapped column is visible in the file.
_ALWAYS_KEYS = {"s4_column", "ecc_column"}
# Dropped when empty to keep 150-field files readable.
_DROP_EMPTY_KEYS = {"transform", "compare"}

_cache: dict[str, tuple[int, MappingDoc]] = {}


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.width = 110
    y.indent(mapping=2, sequence=4, offset=2)
    y.preserve_quotes = True
    y.representer.add_representer(
        type(None), lambda r, _d: r.represent_scalar("tag:yaml.org,2002:null", "null"))
    return y


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# -- paths ----------------------------------------------------------------------

def mapping_path(obj: str) -> Path:
    return paths.MAPPINGS_DIR / f"{obj}.yaml"


def history_dir(obj: str) -> Path:
    return paths.MAPPINGS_DIR / "history" / obj


def changelog_path(obj: str) -> Path:
    return paths.MAPPINGS_DIR / f"{obj}.changelog.jsonl"


def list_objects() -> list[str]:
    if not paths.MAPPINGS_DIR.exists():
        return []
    return sorted(p.stem for p in paths.MAPPINGS_DIR.glob("*.yaml"))


# -- serialisation --------------------------------------------------------------

def _clean(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if v is None and k not in _ALWAYS_KEYS:
                continue
            if k in _DROP_EMPTY_KEYS and v in ([], {}):
                continue
            if k == "on" and v == "source":  # the default for conditions
                continue
            if k in ("numeric", "sql_null") and v is False:
                continue
            out[k] = _clean(v, k)
        return out
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str) and "\n" in value:
        # literal block style can't carry trailing spaces on a line
        return LiteralScalarString("\n".join(line.rstrip() for line in value.split("\n")))
    return value


def to_plain(doc: MappingDoc) -> dict:
    return _clean(doc.model_dump(mode="json", by_alias=True))


def compute_hash(doc: MappingDoc) -> str:
    content = {k: v for k, v in doc.model_dump(mode="json", by_alias=True).items() if k not in _META_KEYS}
    blob = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _merge(old: Any, new: Any) -> Any:
    """Merge `new` (plain data) into `old` (ruamel round-trip data) in place
    where possible, so comments attached to unchanged keys are kept."""
    if isinstance(old, CommentedMap) and isinstance(new, dict):
        for k in [k for k in old.keys() if k not in new]:
            del old[k]
        for pos, (k, v) in enumerate(new.items()):
            if k in old:
                old[k] = _merge(old[k], v)
            else:
                old.insert(min(pos, len(old)), k, v)
        return old
    if isinstance(old, CommentedSeq) and isinstance(new, list):
        def _ided(seq):
            return all(isinstance(x, dict) and "id" in x for x in seq)
        if _ided(new) and _ided(old):
            by_id = {x["id"]: x for x in old}
            out = CommentedSeq()
            for item in new:
                out.append(_merge(by_id[item["id"]], item) if item["id"] in by_id else item)
            return out
        return new
    return new


def to_yaml_text(doc: MappingDoc, base_text: str | None = None) -> str:
    y = _yaml()
    plain = to_plain(doc)
    data: Any = plain
    if base_text:
        existing = y.load(base_text)
        if isinstance(existing, CommentedMap):
            data = _merge(existing, plain)
    buf = io.StringIO()
    y.dump(data, buf)
    return buf.getvalue()


def parse_yaml_text(text: str) -> MappingDoc:
    raw = YAML(typ="safe").load(text)
    return MappingDoc.model_validate(raw)


# -- load -------------------------------------------------------------------------

def load(obj: str) -> MappingDoc:
    """Current mapping for `obj`. Cached per file mtime; returns a copy the
    caller may mutate."""
    p = mapping_path(obj)
    if not p.exists():
        raise MappingNotFound(f"No mapping file for '{obj}' ({p}).")
    mtime = p.stat().st_mtime_ns
    cached = _cache.get(str(p))
    if not cached or cached[0] != mtime:
        doc = parse_yaml_text(p.read_text(encoding="utf-8"))
        _cache[str(p)] = (mtime, doc)
        cached = _cache[str(p)]
    return cached[1].model_copy(deep=True)


def is_hand_edited(doc: MappingDoc) -> bool:
    return bool(doc.content_hash) and doc.content_hash != compute_hash(doc)


def list_versions(obj: str) -> list[dict]:
    out = []
    hd = history_dir(obj)
    if hd.exists():
        for p in sorted(hd.glob("v*.yaml")):
            doc = parse_yaml_text(p.read_text(encoding="utf-8"))
            out.append({"version": doc.mapping_version, "updated_at": doc.updated_at,
                        "updated_by": doc.updated_by, "current": False})
    if mapping_path(obj).exists():
        cur = load(obj)
        out.append({"version": cur.mapping_version, "updated_at": cur.updated_at,
                    "updated_by": cur.updated_by, "current": True})
    return out


def load_version(obj: str, version: int) -> MappingDoc:
    cur = load(obj)
    if cur.mapping_version == version:
        return cur
    p = history_dir(obj) / f"v{version:04d}.yaml"
    if not p.exists():
        raise MappingNotFound(f"Mapping '{obj}' has no version {version}.")
    return parse_yaml_text(p.read_text(encoding="utf-8"))


def read_text(obj: str, version: int | None = None) -> str:
    if version is None:
        return mapping_path(obj).read_text(encoding="utf-8")
    cur = load(obj)
    if cur.mapping_version == version:
        return mapping_path(obj).read_text(encoding="utf-8")
    p = history_dir(obj) / f"v{version:04d}.yaml"
    if not p.exists():
        raise MappingNotFound(f"Mapping '{obj}' has no version {version}.")
    return p.read_text(encoding="utf-8")


# -- save -------------------------------------------------------------------------

def save(doc: MappingDoc, *, updated_by: str, bump: bool = True) -> MappingDoc:
    """Writes `doc` as the new current mapping. The previous file is copied
    to history/ first. Returns the saved document (new version + hash)."""
    MappingDoc.model_validate(doc.model_dump(mode="json", by_alias=True))  # full re-validation
    p = mapping_path(doc.object)
    base_text = None
    if p.exists():
        base_text = p.read_text(encoding="utf-8")
        prev = parse_yaml_text(base_text)
        hd = history_dir(doc.object)
        hd.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, hd / f"v{prev.mapping_version:04d}.yaml")
        if bump:
            doc.mapping_version = prev.mapping_version + 1
    doc.updated_at = now_iso()
    doc.updated_by = updated_by
    # hash what a reader will parse back (serialisation normalises e.g. trailing spaces)
    doc.content_hash = compute_hash(parse_yaml_text(to_yaml_text(doc, base_text)))
    write_text_atomic(p, to_yaml_text(doc, base_text))
    _cache.pop(str(p), None)
    return load(doc.object)


def append_changelog(obj: str, records: list[dict]) -> None:
    for r in records:
        append_jsonl(changelog_path(obj), r)


def read_changelog(obj: str) -> list[dict]:
    return read_jsonl(changelog_path(obj))
