"""
Proposals: the review queue between Agent 1 and the YAML mapping.

  data/proposals/<proposal_id>.json

A proposal holds the typed operations one agent run produced for one
object. Humans accept or reject each operation; "apply" writes only the
accepted ones to the YAML (one new mapping version), in gate order
(G1 mappings before G2 rules) and dependency order. An accepted operation
whose dependency was rejected or failed becomes "stale" instead of being
applied, and every operation is re-validated against the mapping as it is
at apply time, so hand edits or newer versions are respected.

Proposal status: open -> closed (nothing pending) | superseded (a newer run
for the same object replaced it).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from app.core import paths
from app.core.jsonio import read_json, write_json_atomic
from app.mapping import ops as mops
from app.mapping import repository
from app.mapping.model import Provenance


class ProposalNotFound(FileNotFoundError):
    pass


class ProposalError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _path(pid: str):
    return paths.proposals_dir() / f"{pid}.json"


def _save(p: dict) -> None:
    write_json_atomic(_path(p["proposal_id"]), p)


def get(pid: str) -> dict:
    p = read_json(_path(pid))
    if p is None:
        raise ProposalNotFound(f"No proposal '{pid}'.")
    return p


def list_all(obj: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
    d = paths.proposals_dir()
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("P-*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        p = read_json(f)
        if (obj and p["object"] != obj) or (status and p["status"] != status):
            continue
        counts: dict[str, int] = {}
        for op in p["ops"]:
            counts[op["status"]] = counts.get(op["status"], 0) + 1
        out.append({k: p.get(k) for k in ("proposal_id", "object", "created_at", "status", "run_id",
                                          "changeset_id", "base_mapping_version", "mode", "summary")}
                   | {"op_counts": counts, "ops_total": len(p["ops"])})
        if len(out) >= limit:
            break
    return out


def create(obj: str, op_list: list, *, run_id: str | None, changeset_id: str | None, mode: str,
           summary: str, dropped: list[dict] | None = None, base_mapping_version: int | None = None) -> dict:
    """Stores a new open proposal and supersedes older open ones for `obj`.
    Op ids are renumbered (op001, op002, ...) and depends_on remapped."""
    pid = f"P-{obj}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"
    remap = {}
    for i, op in enumerate(op_list, start=1):
        new_id = f"op{i:03d}"
        if op.op_id:
            remap[op.op_id] = new_id
        op.op_id = new_id
    for op in op_list:
        op.depends_on = [remap.get(d, d) for d in op.depends_on]
        op.status = "pending"

    for old in list_all(obj, status="open", limit=1000):
        p = get(old["proposal_id"])
        p["status"] = "superseded"
        p["superseded_by"] = pid
        _save(p)

    p = {
        "proposal_id": pid,
        "object": obj,
        "created_at": _now(),
        "status": "open" if op_list else "closed",
        "run_id": run_id,
        "changeset_id": changeset_id,
        "mode": mode,
        "base_mapping_version": base_mapping_version,
        "summary": summary,
        "ops": [mops.dump_op(op) for op in op_list],
        "dropped_ops": dropped or [],
        "applications": [],
    }
    _save(p)
    return p


def decide(pid: str, decisions: list[dict], by: str | None = None) -> dict:
    """decisions = [{op_id, decision: accepted|rejected, comment?}]"""
    p = get(pid)
    if p["status"] == "superseded":
        raise ProposalError("This proposal was superseded by a newer agent run.")
    by_id = {op["op_id"]: op for op in p["ops"]}
    for d in decisions:
        op = by_id.get(d["op_id"])
        if op is None:
            raise ProposalError(f"Unknown op '{d['op_id']}'.")
        if op["status"] in ("applied",):
            raise ProposalError(f"Op '{d['op_id']}' is already applied.")
        if d["decision"] not in ("accepted", "rejected", "pending"):
            raise ProposalError(f"Invalid decision '{d['decision']}'.")
        op["status"] = d["decision"]
        op["decision"] = None if d["decision"] == "pending" else \
            {"decision": d["decision"], "by": by, "at": _now(), "comment": d.get("comment")}
    _refresh_status(p)
    _save(p)
    return p


def _refresh_status(p: dict) -> None:
    if p["status"] == "superseded":
        return
    p["status"] = "open" if any(op["status"] in ("pending", "accepted") for op in p["ops"]) else "closed"


def _ordered(ops: list[dict]) -> list[dict]:
    """G1 before G2; within that, dependencies first (stable otherwise)."""
    by_id = {o["op_id"]: o for o in ops}
    done, out = set(), []

    def visit(o, stack=()):
        if o["op_id"] in done or o["op_id"] in stack:
            return
        for d in o.get("depends_on", []):
            if d in by_id:
                visit(by_id[d], stack + (o["op_id"],))
        done.add(o["op_id"])
        out.append(o)

    for gate in ("G1", "G2"):
        for o in ops:
            if o["gate"] == gate:
                visit(o)
    return out


def apply(pid: str, by: str | None = None) -> dict:
    p = get(pid)
    if p["status"] == "superseded":
        raise ProposalError("This proposal was superseded by a newer agent run.")
    accepted = [o for o in p["ops"] if o["status"] == "accepted"]
    if not accepted:
        raise ProposalError("No accepted operations to apply.")
    groups: dict[str, list[str]] = {}
    for o in accepted:
        if o.get("alternative_group"):
            groups.setdefault(o["alternative_group"], []).append(o["op_id"])
    clashes = {g: ids for g, ids in groups.items() if len(ids) > 1}
    if clashes:
        raise ProposalError("Accept only one alternative per rule: " +
                            "; ".join(f"{g}: {', '.join(ids)}" for g, ids in clashes.items()))

    cur = repository.load(p["object"])
    hand_edited = repository.is_hand_edited(cur)
    new_version = cur.mapping_version + 1
    work = cur.model_copy(deep=True)
    status_by_id = {o["op_id"]: o["status"] for o in p["ops"]}
    changelog, applied_ids = [], []
    at = _now()

    for od in _ordered(p["ops"]):
        if od["status"] != "accepted":
            continue
        blocked = [d for d in od.get("depends_on", []) if status_by_id.get(d) not in ("applied",)]
        if blocked:
            od["status"], od["result"] = "stale", f"depends on {blocked}, which were not applied"
            status_by_id[od["op_id"]] = "stale"
            continue
        op = mops.parse_op({k: v for k, v in od.items() if k not in ("status", "decision", "preview", "result")})
        prov = Provenance(status="approved", confidence=op.confidence, reason=op.reason[:500],
                          version=new_version,
                          source=op.source if op.source in ("databricks", "workbook") else "agent",
                          proposal_id=pid, approved_by=by, approved_at=at)
        before = mops.preview(work, op)
        trial = work.model_copy(deep=True)
        try:
            mops.apply_op(trial, op, prov)
            trial = mops.revalidate(trial)
        except mops.OpError as exc:
            od["status"], od["result"] = "failed", str(exc)
            status_by_id[od["op_id"]] = "failed"
            continue
        work = trial
        od["status"], od["result"] = "applied", f"applied in mapping v{new_version}"
        status_by_id[od["op_id"]] = "applied"
        applied_ids.append(od["op_id"])
        changelog.append({
            "at": at, "object": p["object"], "mapping_version": new_version, "proposal_id": pid,
            "op_id": od["op_id"], "op": od["op"], "gate": od["gate"], "target": before.get("target"),
            "before": before.get("before"), "after": before.get("after"), "decided_by": by,
            "confidence": od.get("confidence"), "reason": od.get("reason"),
        })

    saved_version = None
    if applied_ids:
        saved = repository.save(work, updated_by=f"proposal:{pid}")
        saved_version = saved.mapping_version
        if hand_edited:
            changelog.insert(0, {"at": at, "object": p["object"], "mapping_version": saved_version,
                                 "proposal_id": pid, "op_id": None, "op": "manual_edit_detected",
                                 "target": f"{p['object']}.yaml", "before": None, "after": None,
                                 "decided_by": None, "reason": "The YAML was edited outside the app; "
                                 "those edits are included in this version."})
        repository.append_changelog(p["object"], changelog)
    p.setdefault("applications", []).append({"at": at, "by": by, "mapping_version": saved_version,
                                             "applied": applied_ids})
    _refresh_status(p)
    _save(p)
    return p
