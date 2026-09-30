"""
Versioned local copies of the SharePoint extracts.

SharePoint only ever holds the latest files, so every refresh that brings
something new is stored here as a version:

  data/snapshots/<OBJECT>/<SIDE>/
    versions.json                 {"current": "<vid>", "previous": ["<vid>", ...]}
    <vid>/manifest.json           where the files came from + change metadata
    <vid>/raw/<original files>    as downloaded (SNAPSHOT_KEEP_RAW)
    <vid>/combined.csv            all parts concatenated, text only, + lineage columns
    <vid>/profile.json            column profile (computed once, used by the diff)
  data/archive/<OBJECT>/<SIDE>/<vid>.zip

Only "current" and SNAPSHOT_KEEP_PREVIOUS earlier versions stay live; older
ones are zipped into the archive (kept for later, never used for diffing).

manifest.json:
  dataset, version_id, previous_version_id, refresh_run_id, created_at,
  source {kind: sharepoint|local-source|local|legacy_import, folder_path, prefix, site_id},
  listing_fingerprint, files [{name, part_label, item_id, eTag, cTag,
  lastModifiedDateTime, size, quickXorHash, sha256, downloaded_at, rows,
  cols, local_path}], combined {path, rows, cols, columns, sha256}
"""
from __future__ import annotations

import hashlib
import secrets
import shutil
import zipfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import pandas as pd

from app.core import paths
from app.core.config import settings
from app.core.jsonio import read_json, write_json_atomic

COMBINED_NAME = "combined.csv"


class SnapshotNotFound(FileNotFoundError):
    pass


def new_version_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"v{ts}-{secrets.token_hex(2)}"


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def listing_fingerprint(items: list[dict]) -> str:
    """Hash of what SharePoint says about the matching files. Content-based
    fields only (cTag / quickXorHash change with content; eTag also changes
    on metadata-only edits, so it is not used)."""
    rows = sorted(
        (i.get("name", ""), i.get("id", ""),
         ((i.get("file") or {}).get("hashes") or {}).get("quickXorHash") or i.get("cTag") or "",
         str(i.get("size", "")))
        for i in items
    )
    return "sha256:" + hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


# -- layout -------------------------------------------------------------------------

def dataset_dir(obj: str, side: str) -> Path:
    return paths.snapshots_dir() / obj / side


def version_dir(obj: str, side: str, vid: str) -> Path:
    return dataset_dir(obj, side) / vid


def _versions_path(obj: str, side: str) -> Path:
    return dataset_dir(obj, side) / "versions.json"


def versions(obj: str, side: str) -> dict:
    return read_json(_versions_path(obj, side), default={"current": None, "previous": []})


def resolve_version(obj: str, side: str, vid: str | None = None) -> str:
    """vid None / 'current' -> current; 'previous' -> the one before it."""
    v = versions(obj, side)
    if vid in (None, "current"):
        if not v["current"]:
            raise SnapshotNotFound(f"No data stored for {obj}/{side} yet - run a refresh first.")
        return v["current"]
    if vid == "previous":
        if not v["previous"]:
            raise SnapshotNotFound(f"{obj}/{side} has no previous version.")
        return v["previous"][0]
    if vid != v["current"] and vid not in v["previous"]:
        raise SnapshotNotFound(f"{obj}/{side} has no live version '{vid}' (it may be archived).")
    return vid


def manifest(obj: str, side: str, vid: str | None = None) -> dict:
    vid = resolve_version(obj, side, vid)
    m = read_json(version_dir(obj, side, vid) / "manifest.json")
    if m is None:
        raise SnapshotNotFound(f"Manifest missing for {obj}/{side}/{vid}.")
    return m


def current_manifest(obj: str, side: str) -> dict | None:
    try:
        return manifest(obj, side)
    except SnapshotNotFound:
        return None


def combined_path(obj: str, side: str, vid: str | None = None) -> Path:
    vid = resolve_version(obj, side, vid)
    return version_dir(obj, side, vid) / COMBINED_NAME


@lru_cache(maxsize=8)
def _read_cached(path: str, mtime_ns: int) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, low_memory=False)


def read_combined(obj: str, side: str, vid: str | None = None) -> pd.DataFrame:
    """The combined frame for a version (a copy; safe to mutate)."""
    p = combined_path(obj, side, vid)
    return _read_cached(str(p), p.stat().st_mtime_ns).copy()


def read_profile(obj: str, side: str, vid: str | None = None) -> dict | None:
    vid = resolve_version(obj, side, vid)
    return read_json(version_dir(obj, side, vid) / "profile.json")


def archived(obj: str, side: str) -> list[str]:
    d = paths.archive_dir() / obj / side
    return sorted((p.stem for p in d.glob("*.zip")), reverse=True) if d.exists() else []


# -- writing -----------------------------------------------------------------------

def begin(obj: str, side: str) -> tuple[str, Path]:
    """Creates a staging folder for a new version. Commit or discard it."""
    vid = new_version_id()
    staging = dataset_dir(obj, side) / f".tmp-{vid}"
    (staging / "raw").mkdir(parents=True, exist_ok=True)
    return vid, staging


def discard(staging: Path) -> None:
    shutil.rmtree(staging, ignore_errors=True)


def commit(obj: str, side: str, vid: str, staging: Path, manifest_data: dict,
           combined: pd.DataFrame, profile: dict | None) -> dict:
    """Writes combined.csv / profile.json / manifest.json into the staging
    folder, moves it into place, makes it current and applies retention."""
    v = versions(obj, side)
    combined_file = staging / COMBINED_NAME
    combined.to_csv(combined_file, index=False)
    manifest_data = dict(manifest_data)
    manifest_data.update({
        "dataset": f"{obj}/{side}",
        "version_id": vid,
        "previous_version_id": v["current"],
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "combined": {
            "path": COMBINED_NAME,
            "rows": int(len(combined)),
            "cols": int(len(combined.columns)),
            "columns": [str(c) for c in combined.columns],
            "sha256": sha256_file(combined_file),
        },
    })
    if profile is not None:
        write_json_atomic(staging / "profile.json", profile)
    write_json_atomic(staging / "manifest.json", manifest_data)

    final = version_dir(obj, side, vid)
    staging.rename(final)

    previous = ([v["current"]] if v["current"] else []) + list(v["previous"])
    keep = max(settings.SNAPSHOT_KEEP_PREVIOUS, 0)
    live, to_archive = previous[:keep], previous[keep:]
    write_json_atomic(_versions_path(obj, side), {"current": vid, "previous": live})
    for old in to_archive:
        _archive(obj, side, old)
    return manifest_data


def _archive(obj: str, side: str, vid: str) -> None:
    src = version_dir(obj, side, vid)
    if not src.exists():
        return
    dest_dir = paths.archive_dir() / obj / side
    dest_dir.mkdir(parents=True, exist_ok=True)
    zpath = dest_dir / f"{vid}.zip"
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in src.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(src))
    shutil.rmtree(src)


def list_datasets(objects: list[str], sides: tuple[str, ...]) -> list[dict]:
    out = []
    for obj in objects:
        for side in sides:
            v = versions(obj, side)
            live = []
            for vid in ([v["current"]] if v["current"] else []) + v["previous"]:
                m = read_json(version_dir(obj, side, vid) / "manifest.json") or {}
                live.append({
                    "version_id": vid,
                    "current": vid == v["current"],
                    "created_at": m.get("created_at"),
                    "source": (m.get("source") or {}).get("kind"),
                    "files": len(m.get("files", [])),
                    "rows": (m.get("combined") or {}).get("rows"),
                    "cols": (m.get("combined") or {}).get("cols"),
                    "refresh_run_id": m.get("refresh_run_id"),
                })
            out.append({"object": obj, "side": side, "versions": live,
                        "archived": archived(obj, side)})
    return out
