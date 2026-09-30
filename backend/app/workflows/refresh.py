"""
The "Refresh data" workflow, started from the UI:

  list      list the SharePoint folders once
  download  for each dataset whose files changed, download its part-files
  combine   concatenate them (text only, with lineage columns)
  profile   profile the combined data
            -> stored as a new snapshot version (retention: current + N previous)
  diff      for each object with a changed dataset, build a ChangeSet
  agent     run Agent 1 on it -> proposal for human review

Nothing changed (same SharePoint fingerprints / same bytes) means no new
version, no diff and no agent run - unless `force` is set. A dataset whose
files can't be found keeps its current version and is reported as an error.

Progress is written to data/refresh_runs/<id>.json after every step, so
the UI can poll it.
"""
from __future__ import annotations

import secrets
import traceback
from datetime import datetime, timezone

from app.connectors import get_client
from app.core import paths
from app.core.config import settings
from app.core.jsonio import read_json, write_json_atomic
from app.core.locks import FileLock, LockBusy
from app.ingest.combine import combine
from app.ingest.readers import read_bytes
from app.ingest.sources import OBJECTS, REF, SOURCES, part_label
from app.profiling.profiler import profile_frame
from app.store import snapshot_store as ss

STAGES = ["list", "download", "combine", "profile", "diff", "agent", "done"]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _path(run_id: str):
    return paths.refresh_runs_dir() / f"{run_id}.json"


def _save(rec: dict) -> None:
    write_json_atomic(_path(rec["refresh_run_id"]), rec)


def get(run_id: str) -> dict | None:
    return read_json(_path(run_id))


def list_runs(limit: int = 20) -> list[dict]:
    d = paths.refresh_runs_dir()
    if not d.exists():
        return []
    files = sorted(d.glob("R-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [read_json(p) for p in files]


def create(force: bool = False, objects: list[str] | None = None, run_agent: bool = True) -> dict:
    run_id = f"R-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"
    rec = {
        "refresh_run_id": run_id, "created_at": _now(), "status": "queued", "stage": None,
        "force": force, "objects": objects or OBJECTS, "run_agent": run_agent,
        "datasets": {}, "changesets": {}, "agent_runs": {}, "messages": [], "error": None,
    }
    _save(rec)
    return rec


def _stage(rec: dict, stage: str, message: str | None = None) -> None:
    rec["stage"] = stage
    if message:
        rec["messages"].append({"at": _now(), "stage": stage, "message": message})
    _save(rec)


def _file_meta(item: dict) -> dict:
    return {
        "name": item.get("name"),
        "part_label": part_label(item.get("name", "")),
        "item_id": item.get("id"),
        "eTag": item.get("eTag"),
        "cTag": item.get("cTag"),
        "lastModifiedDateTime": item.get("lastModifiedDateTime"),
        "size": item.get("size"),
        "quickXorHash": ((item.get("file") or {}).get("hashes") or {}).get("quickXorHash"),
    }


def local_items(spec) -> list[dict]:
    """Files for `spec` in the local drop folder (data/incoming/), shaped like
    Graph items so the rest of the refresh treats them the same way."""
    folder = paths.incoming_dir()
    if not spec.allow_local or not folder.exists():
        return []
    out = []
    for f in sorted(folder.iterdir()):
        if f.is_file() and spec.matches(f.name):
            st = f.stat()
            out.append({"id": f"local:{f}", "name": f.name, "size": st.st_size,
                        "cTag": f"local:{st.st_size}:{st.st_mtime_ns}", "file": {},
                        "lastModifiedDateTime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    return out


def _download(client, site_id: str, item: dict) -> bytes:
    if str(item["id"]).startswith("local:"):
        return open(item["id"][len("local:"):], "rb").read()
    return client.download_file(site_id, item["id"])


def _refresh_dataset(client, site_id: str, spec, items: list[dict], rec: dict) -> str:
    """Returns 'changed' | 'unchanged' | 'missing' | 'error'."""
    ds = rec["datasets"][spec.dataset]
    matching = sorted((i for i in items if "file" in i and spec.matches(i.get("name", ""))),
                      key=lambda i: i.get("name", ""))
    origin = "local-source" if settings.SOURCE_MODE == "local" else "sharepoint"
    if not matching:
        matching = local_items(spec)
        origin = "local"
    if not matching:
        where = f"'{spec.folder or '(library root)'}'" + (" or data/incoming/" if spec.allow_local else "")
        if not spec.required:
            cur = ss.current_manifest(spec.object, spec.side)
            ds.update(status="missing" if not cur else "kept",
                      reason=f"optional table not found in {where}"
                             + ("" if not cur else "; the stored version is kept"),
                      version_id=cur["version_id"] if cur else None)
            if cur:
                # it was there before: say so loudly - the rules keep using an old copy
                rec.setdefault("warnings", []).append(
                    f"{spec.dataset}: the file is no longer in {where}; rules keep using the stored version "
                    f"{cur['version_id']} (stored {cur.get('created_at', '?')}).")
            return "missing" if not cur else "unchanged"
        ds.update(status="error", error=f"No files matching '{spec.prefix}*{'/'.join(spec.extensions)}' in "
                                        f"{where}. The current version is kept.")
        return "error"
    ds["origin"] = origin
    fp = ss.listing_fingerprint(matching)
    cur = ss.current_manifest(spec.object, spec.side)
    ds["files"] = [i.get("name") for i in matching]
    if cur and not rec["force"] and cur.get("listing_fingerprint") == fp:
        ds.update(status="unchanged", version_id=cur["version_id"], reason="SharePoint fingerprint unchanged")
        return "unchanged"

    _stage(rec, "download", f"{spec.dataset}: downloading {len(matching)} file(s)")
    vid, staging = ss.begin(spec.object, spec.side)
    try:
        parts, files = [], []
        for item in matching:
            content = _download(client, site_id, item)
            meta = _file_meta(item)
            meta["sha256"] = ss.sha256_bytes(content)
            meta["downloaded_at"] = _now()
            if settings.SNAPSHOT_KEEP_RAW:
                (staging / "raw" / meta["name"]).write_bytes(content)
                meta["local_path"] = f"raw/{meta['name']}"
            df = read_bytes(content, spec.reader_for(meta["name"]))
            meta["rows"], meta["cols"] = int(len(df)), int(len(df.columns))
            parts.append((meta["name"], meta["part_label"], df))
            files.append(meta)

        if cur:
            # identical bytes never make a new version - not even when forced, so a forced
            # refresh can't push real history out of retention (current + N previous)
            before = {f["name"]: f.get("sha256") for f in cur.get("files", [])}
            after = {f["name"]: f["sha256"] for f in files}
            if before == after:
                ss.discard(staging)
                reason = ("forced re-read: file contents identical, current version kept" if rec["force"]
                          else "file contents unchanged")
                ds.update(status="unchanged", version_id=cur["version_id"], reason=reason)
                return "unchanged"

        _stage(rec, "combine", f"{spec.dataset}: combining")
        combined = combine(parts)
        _stage(rec, "profile", f"{spec.dataset}: profiling {len(combined)} rows")
        profile = profile_frame(combined)
        manifest = ss.commit(spec.object, spec.side, vid, staging, {
            "refresh_run_id": rec["refresh_run_id"],
            "source": {"kind": origin, "site_id": site_id if origin == "sharepoint" else None,
                       "folder_path": str(paths.incoming_dir()) if origin == "local" else spec.folder,
                       "prefix": spec.prefix},
            "listing_fingerprint": fp,
            "files": files,
        }, combined, profile)
    except BaseException:
        ss.discard(staging)
        raise
    ds.update(status="changed", version_id=vid, rows=manifest["combined"]["rows"],
              cols=manifest["combined"]["cols"], previous_version_id=manifest["previous_version_id"])
    return "changed"


def execute(run_id: str) -> dict:
    from app.agents.rulebook_agent import agent
    from app.diff import changeset

    rec = get(run_id)
    rec["status"], rec["started_at"] = "running", _now()
    _save(rec)
    lock = FileLock(paths.refresh_lock_path(), owner=run_id)
    try:
        lock.acquire()
    except LockBusy as exc:
        rec.update(status="failed", error=str(exc), ended_at=_now())
        _save(rec)
        return rec
    try:
        client = get_client()
        _stage(rec, "list", "Reading the local source folder" if settings.SOURCE_MODE == "local"
               else "Connecting to SharePoint")
        site_id = client.get_site_id()
        listings: dict[str, list[dict]] = {}
        specs = [s for s in SOURCES if s.object in rec["objects"] or s.object == REF]
        for spec in specs:
            if spec.folder not in listings:
                listings[spec.folder] = client.list_folder_items(site_id, spec.folder)
        statuses: dict[str, dict[str, str]] = {}
        for spec in specs:
            rec["datasets"][spec.dataset] = {"object": spec.object, "side": spec.side, "status": "running"}
            _save(rec)
            status = _refresh_dataset(client, site_id, spec, listings[spec.folder], rec)
            statuses.setdefault(spec.object, {})[spec.side] = status
            _save(rec)

        todo = []
        refs_changed = "changed" in statuses.get(REF, {}).values()
        for obj in rec["objects"]:
            st = statuses.get(obj, {})
            has_both = all(ss.current_manifest(obj, side) for side in ("ECC", "S4"))
            if not has_both:
                rec["messages"].append({"at": _now(), "stage": "diff",
                                        "message": f"{obj}: skipped - ECC and S/4 data are both needed"})
            elif "changed" in st.values() or refs_changed or rec["force"]:
                todo.append(obj)
        if not todo:
            rec.update(status="no_changes", stage="done", ended_at=_now())
            src = "the local source folder" if settings.SOURCE_MODE == "local" else "SharePoint"
            msg = f"No changes in {src} - nothing to diff, agent not run."
            if rec.get("warnings"):
                msg = f"No data changes in {src}, but {len(rec['warnings'])} warning(s) - see above. Agent not run."
            rec["messages"].append({"at": _now(), "stage": "done", "message": msg})
            _save(rec)
            return rec

        for obj in todo:
            _stage(rec, "diff", f"{obj}: computing changes")
            cs = changeset.build(obj, refresh_run_id=run_id, dataset_status=statuses.get(obj),
                                 ref_status=statuses.get(REF))
            rec["changesets"][obj] = cs["changeset_id"]
            _save(rec)
        if rec["run_agent"]:
            for obj in todo:
                _stage(rec, "agent", f"{obj}: Agent 1 is reviewing the changes")
                try:
                    run = agent.run(obj, rec["changesets"][obj], refresh_run_id=run_id)
                    rec["agent_runs"][obj] = {"run_id": run["run_id"], "proposal_id": run.get("proposal_id"),
                                              "status": run["status"], "operations": run.get("operations"),
                                              "notes": run.get("notes")}
                except Exception as exc:  # noqa: BLE001 - one object's agent failure shouldn't hide the others
                    rec["agent_runs"][obj] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                _save(rec)
        rec.update(status="completed", stage="done", ended_at=_now())
        _save(rec)
    except Exception as exc:  # noqa: BLE001 - surfaced in the run record
        rec.update(status="failed", error=f"{type(exc).__name__}: {exc}", ended_at=_now(),
                   traceback=traceback.format_exc()[-4000:])
        _save(rec)
    finally:
        lock.release()
    return rec


def rerun_agent(obj: str) -> dict:
    """Manual 'Re-run agent': fresh ChangeSet against the current mapping, then Agent 1."""
    from app.agents.rulebook_agent import agent
    from app.diff import changeset

    cs = changeset.build(obj)
    return agent.run(obj, cs["changeset_id"])
