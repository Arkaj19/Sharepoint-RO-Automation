"""
A ChangeSet is everything Agent 1 (and the Changes page) needs to know
about one migration object after a refresh:

  datasets        which versions were compared, per side (and per changed
                  supporting table: "REF/<TABLE>")
  changes         same-side diffs (previous -> current), each tagged with the
                  mapped fields / value maps it affects, ranked by severity.
                  A changed supporting table the object's lookups use is
                  diffed too (side "REF/<TABLE>"), tagged with the lookup and
                  the fields whose rules read it
  alignment       how current ECC and S/4 rows pair up (key setup, match share)
  mapping_health  gaps in the current mapping against the current data:
                  unmapped fields, broken column names, unmapped columns,
                  crosswalk gaps, fields whose values no longer agree,
                  expected vs actual row counts

The object's first change set (nothing diffed, no earlier change set) is a
baseline: `changes` is empty and the mapping health drives the bootstrap
proposal. Later change sets are incremental even when nothing was diffed.

Stored at data/changesets/<id>.json.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from app.core import paths
from app.core.jsonio import read_json, write_json_atomic
from app.diff import datasets as dd
from app.inference import keys as inf_keys
from app.inference.evaluate import expected_row_count, field_agreement
from app.ingest.readers import data_columns
from app.mapping import repository
from app.mapping.model import MappingDoc, ValueMapStep, iter_steps
from app.store import snapshot_store as ss
from app.transforms import engine

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
LOW_AGREEMENT = 0.95
SIDES = {"ECC": "ecc", "S4": "s4"}


class ChangeSetNotFound(FileNotFoundError):
    pass


def _new_id(obj: str) -> str:
    return f"CS-{obj}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"


def _column_to_field(doc: MappingDoc, side: str) -> dict[str, str]:
    """Extract column -> field id. ECC columns are resolved like the engine
    does (explicit ecc_column, or source_ref through the dictionary)."""
    if side == "ECC":
        return {c: f.id for f in doc.fields if (c := doc.own_ecc_column_of(f))}
    return {f.s4_column: f.id for f in doc.fields if f.s4_column}


def _value_maps_of(doc: MappingDoc, field_id: str) -> list[str]:
    f = doc.field(field_id)
    return [s.map for s in iter_steps(f.transform) if isinstance(s, ValueMapStep)] if f else []


def tag_impacts(changes: list[dict], doc: MappingDoc) -> None:
    for ch in changes:
        colmap = _column_to_field(doc, ch["side"])
        impacts: list[str] = []
        kind = ch["kind"]
        if kind in ("column_removed", "column_added", "blank_rate_changed", "type_changed",
                    "distinct_changed", "removed_code_values"):
            fid = colmap.get(ch.get("column"))
            if fid:
                impacts.append(f"field:{fid}")
        elif kind == "column_renamed":
            fid = colmap.get(ch["from"])
            if fid:
                impacts.append(f"field:{fid}")
        elif kind == "new_code_values":
            fid = colmap.get(ch["column"])
            if fid:
                impacts.append(f"field:{fid}")
                impacts += [f"value_map:{m}" for m in _value_maps_of(doc, fid)]
        elif kind == "length_increased" and ch["side"] == "S4":
            fid = colmap.get(ch["column"])
            f = doc.field(fid) if fid else None
            if f and f.length and ch["after"] > f.length:
                impacts.append(f"field:{fid}")
                ch["severity"] = "high"
                ch["exceeds_mapping_length"] = f.length
        elif kind == "cells_changed":
            for c in ch["columns"]:
                fid = colmap.get(c["column"])
                if fid:
                    c["field"] = fid
                    impacts.append(f"field:{fid}")
        elif kind in ("keys_added", "keys_removed", "duplicate_keys"):
            impacts.append("key")
        ch["impacts"] = sorted(set(impacts))
        if ch["impacts"] and ch["severity"] == "low":
            ch["severity"] = "medium"


def mapping_health(doc: MappingDoc, ecc_df, s4_df, al: inf_keys.Alignment) -> dict:
    ecc_cols, s4_cols = set(data_columns(ecc_df)), set(data_columns(s4_df))
    fanout = set(doc.fanout_fields()) | set(doc.routed_fields())
    unmapped_fields, broken = [], []
    for f in doc.fields:
        own = doc.own_ecc_column_of(f)
        if not doc.has_source(f) and f.id not in fanout and not doc.is_derived_only(f):
            unmapped_fields.append(f.id)
        elif f.source_ref and not doc.ecc_column_of(f):
            broken.append({"field": f.id, "side": "ECC", "column": f.source_ref, "issue": "not in the dictionary"})
        if own and own not in ecc_cols:
            broken.append({"field": f.id, "side": "ECC", "column": own})
        if f.s4_column and f.s4_column not in s4_cols:
            broken.append({"field": f.id, "side": "S4", "column": f.s4_column})
    used_ecc = {c for f in doc.fields if (c := doc.own_ecc_column_of(f))} | doc.ignored("ecc")
    used_ecc |= set((doc.dictionary.get(doc.alias()) or {}).values())
    used_s4 = {f.s4_column for f in doc.fields if f.s4_column} | doc.ignored("s4")

    gaps = {}
    for fid in doc.key.fields:
        f = doc.field(fid)
        maps = _value_maps_of(doc, fid)
        col = doc.own_ecc_column_of(f) if f else None
        if not f or not col or col not in ecc_cols or not maps:
            continue
        sources = set(doc.value_maps[maps[0]].targets().keys())
        codes = engine.as_text(ecc_df[col]).str.strip()
        missing = codes[~codes.isin(sources) & (codes != "")].value_counts()
        if len(missing):
            gaps[fid] = {"value_map": maps[0], "unmapped_values": {str(k): int(v) for k, v in missing.items()}}

    low = []
    if len(al.pairs):
        for f in doc.fields:
            if not (doc.has_source(f) or doc.is_derived_only(f)) or not f.s4_column or f.s4_column not in s4_cols:
                continue
            if f.id in doc.key.fields:
                continue
            res = field_agreement(al.doc, al, f.id, sample=3)
            if res.get("agreement") is not None and res["support"] >= 20 and res["agreement"] < LOW_AGREEMENT:
                low.append({"field": f.id, **res})
    try:
        rows = expected_row_count(doc, ecc_df, s4_df) if not fanout or all(
            doc.own_ecc_column_of(doc.field(k)) for k in fanout) else None
    except (KeyError, ValueError) as exc:
        rows = {"error": str(exc)}
    return {
        "fields_total": len(doc.fields),
        "fields_with_ecc_column": sum(1 for f in doc.fields if doc.has_source(f) or doc.is_derived_only(f)),
        "missing_inputs": engine.missing_inputs(al.ecc),
        "unmapped_fields": unmapped_fields,
        "broken_columns": broken,
        "unmapped_ecc_columns": sorted(ecc_cols - used_ecc),
        "unmapped_s4_columns": sorted(s4_cols - used_s4),
        "crosswalk_gaps": gaps,
        "low_agreement_fields": low,
        "row_counts": rows,
    }


def _key_setup_for_side(al: inf_keys.Alignment, side: str) -> tuple[list[str], list]:
    cols, norms = [], []
    for fid in al.doc.key.fields:
        f = al.doc.field(fid)
        col = al.doc.own_ecc_column_of(f) if side == "ECC" else f.s4_column
        if not col:
            return [], []
        cols.append(col)
        steps = f.compare
        norms.append((lambda s, st=steps, d=al.doc: engine.apply_steps(s, st, doc=d).values) if steps else None)
    return cols, norms


def _fields_using(doc: MappingDoc, lookup_id: str) -> list[str]:
    """Fields whose source or rules reference `<lookup_id>.<FIELD>`."""
    pat = f"{lookup_id.upper()}."
    out = []
    for f in doc.fields:
        blob = json.dumps(f.model_dump(mode="json", include={"source_ref", "transform", "compare"}), default=str)
        if pat in blob.upper():
            out.append(f.id)
    return out


def _ref_changes(doc: MappingDoc, ref_status: dict | None, next_id) -> tuple[list[dict], dict]:
    """Diffs of the supporting tables that changed in this refresh and that
    this object's lookups read."""
    from app.ingest.sources import REF
    changes, info = [], {}
    by_table: dict[str, list] = {}
    for lk in doc.lookups:
        by_table.setdefault(lk.table.upper(), []).append(lk)
    for table, st in (ref_status or {}).items():
        if st == "changed" and table.upper() == "RULE_WORKBOOK":
            # the business workbook isn't joined by any rule; its logic is compared with the SQL on import
            v = ss.versions(REF, table)
            info[f"{REF}/{table}"] = {"from": (v["previous"] or [None])[0], "to": v["current"], "status": "changed"}
            changes.append({"id": next_id(), "kind": "workbook_changed", "side": f"{REF}/{table}", "severity": "medium",
                            "impacts": [], "hint": "Re-import the logic (Mapping -> Import as proposal) to check "
                                                   "the rules against the updated workbook."})
            continue
        lks = by_table.get(table.upper())
        if st != "changed" or not lks:
            continue
        v = ss.versions(REF, table)
        prev = v["previous"][0] if v["previous"] else None
        side = f"{REF}/{table}"
        info[side] = {"from": prev, "to": v["current"], "status": "changed" if prev else "added",
                      "lookups": [lk.id for lk in lks]}
        impacts = sorted({f"lookup:{lk.id}" for lk in lks}
                         | {f"field:{fid}" for lk in lks for fid in _fields_using(doc, lk.id)})
        if not prev:
            changes.append({"id": next_id(), "kind": "table_added", "side": side, "table": table,
                            "rows": (ss.manifest(REF, table) or {}).get("combined", {}).get("rows"),
                            "severity": "high", "impacts": impacts})
            continue
        found: list[dict] = []
        prev_m, cur_m = ss.manifest(REF, table, prev), ss.manifest(REF, table)
        found += dd.diff_files(prev_m, cur_m, side, next_id)
        prev_p, cur_p = ss.read_profile(REF, table, prev), ss.read_profile(REF, table)
        if prev_p and cur_p:
            found += dd.diff_schema(prev_p, cur_p, side, next_id)
            found += dd.diff_profiles(prev_p, cur_p, side, next_id)
        # cells: keyed like the lookup joins (keys + where fields), resolved through the dictionary
        labels = doc.dictionary.get(table.upper()) or {}
        lk = lks[0]
        key_cols = [labels.get(k.right.upper(), k.right) for k in lk.keys] +                    [labels.get(w.field.upper(), w.field) for w in lk.where]
        found += dd.diff_keys(ss.read_combined(REF, table, prev), ss.read_combined(REF, table),
                              key_cols, [None] * len(key_cols), side, next_id)
        for ch in found:
            ch["impacts"] = impacts
            if impacts and ch["severity"] == "low" and ch["kind"] != "file_changed":
                ch["severity"] = "medium"
        changes += found
    return changes, info


def build(obj: str, refresh_run_id: str | None = None, dataset_status: dict | None = None,
          ref_status: dict | None = None) -> dict:
    doc = repository.load(obj)
    ecc_df = ss.read_combined(obj, "ECC")
    s4_df = ss.read_combined(obj, "S4")
    al = inf_keys.align(doc, ecc_df, s4_df)
    next_id = dd._Ids("C")

    changes: list[dict] = []
    ds_info = {}
    baseline = True
    for side in ("ECC", "S4"):
        v = ss.versions(obj, side)
        prev = v["previous"][0] if v["previous"] else None
        status = (dataset_status or {}).get(side) or ("changed" if prev else "baseline")
        ds_info[side] = {"from": prev, "to": v["current"], "status": status}
        if not prev or status == "unchanged":
            continue
        baseline = False
        prev_m, cur_m = ss.manifest(obj, side, prev), ss.manifest(obj, side)
        prev_p, cur_p = ss.read_profile(obj, side, prev), ss.read_profile(obj, side)
        changes += dd.diff_files(prev_m, cur_m, side, next_id)
        if prev_p and cur_p:
            changes += dd.diff_schema(prev_p, cur_p, side, next_id)
            changes += dd.diff_profiles(prev_p, cur_p, side, next_id)
        cols, norms = _key_setup_for_side(al, side)
        changes += dd.diff_keys(ss.read_combined(obj, side, prev), ss.read_combined(obj, side),
                                cols, norms, side, next_id)
    tag_impacts(changes, doc)
    ref_changes, ref_info = _ref_changes(doc, ref_status, next_id)
    changes += ref_changes
    ds_info.update(ref_info)
    if ref_info or list_all(obj, limit=1):
        baseline = False                # a later change set is incremental even if only a REF table changed
    changes.sort(key=lambda c: (SEVERITY_RANK.get(c["severity"], 3), -len(c.get("impacts", []))))

    health = mapping_health(doc, ecc_df, s4_df, al)
    summary: dict = {"changes": len(changes), "by_severity": {}, "by_kind": {},
                     "mapping_impacts": sum(1 for c in changes if c["impacts"])}
    for c in changes:
        summary["by_severity"][c["severity"]] = summary["by_severity"].get(c["severity"], 0) + 1
        summary["by_kind"][c["kind"]] = summary["by_kind"].get(c["kind"], 0) + 1
    summary.update({
        "unmapped_fields": len(health["unmapped_fields"]),
        "broken_columns": len(health["broken_columns"]),
        "low_agreement_fields": len(health["low_agreement_fields"]),
        "crosswalk_gaps": sum(len(g["unmapped_values"]) for g in health["crosswalk_gaps"].values()),
    })

    cs = {
        "changeset_id": _new_id(obj),
        "object": obj,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "refresh_run_id": refresh_run_id,
        "baseline": baseline,
        "mapping_version": doc.mapping_version,
        "datasets": ds_info,
        "summary": summary,
        "changes": changes,
        "alignment": {"method": al.method, "stats": al.stats, "notes": al.notes, "key_setup": al.key_setup},
        "mapping_health": health,
    }
    write_json_atomic(paths.changesets_dir() / f"{cs['changeset_id']}.json", cs)
    return cs


def load(changeset_id: str) -> dict:
    cs = read_json(paths.changesets_dir() / f"{changeset_id}.json")
    if cs is None:
        raise ChangeSetNotFound(f"No change set '{changeset_id}'.")
    return cs


def list_all(obj: str | None = None, limit: int = 50) -> list[dict]:
    d = paths.changesets_dir()
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("CS-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        cs = read_json(p)
        if obj and cs["object"] != obj:
            continue
        out.append({k: cs[k] for k in ("changeset_id", "object", "created_at", "refresh_run_id",
                                       "baseline", "mapping_version", "datasets", "summary")})
        if len(out) >= limit:
            break
    return out


def latest(obj: str) -> dict | None:
    items = list_all(obj, limit=1)
    return load(items[0]["changeset_id"]) if items else None


def for_llm(cs: dict, max_chars: int = 12000) -> str:
    """Compact JSON for the model: summary, ranked changes, alignment and
    health, trimmed until it fits. The agent pulls details through tools."""
    health = dict(cs["mapping_health"])
    compact = {
        "changeset_id": cs["changeset_id"], "object": cs["object"], "baseline": cs["baseline"],
        "mapping_version": cs["mapping_version"], "datasets": cs["datasets"], "summary": cs["summary"],
        "alignment": {"method": cs["alignment"]["method"], "stats": cs["alignment"]["stats"],
                      "notes": cs["alignment"]["notes"]},
        "mapping_health": {
            **{k: health[k] for k in ("fields_total", "fields_with_ecc_column", "crosswalk_gaps", "row_counts")},
            "unmapped_fields": health["unmapped_fields"],
            "broken_columns": health["broken_columns"],
            "unmapped_ecc_columns_count": len(health["unmapped_ecc_columns"]),
            "unmapped_s4_columns": health["unmapped_s4_columns"],
            "low_agreement_fields": [{k: v for k, v in f.items() if k != "mismatch_samples"}
                                     for f in health["low_agreement_fields"]],
        },
        "changes": cs["changes"],
    }
    text = json.dumps(compact, ensure_ascii=False, default=str)
    while len(text) > max_chars and compact["changes"]:
        compact["changes"] = compact["changes"][: max(len(compact["changes"]) // 2, 0)]
        compact["changes_truncated"] = True
        text = json.dumps(compact, ensure_ascii=False, default=str)
    if len(text) > max_chars:
        mh = compact["mapping_health"]
        mh["unmapped_fields"] = mh["unmapped_fields"][:40]
        mh["low_agreement_fields"] = mh["low_agreement_fields"][:20]
        compact["truncated"] = True
        text = json.dumps(compact, ensure_ascii=False, default=str)
    return text
