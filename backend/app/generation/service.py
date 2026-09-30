"""
Generation: produce the S/4 load data ourselves from the ECC extract,
the supporting tables and the approved YAML rules - no Databricks needed.

  generate(doc, ecc)          -> S/4-shaped frame (one column per mapped S/4 field)
  shadow_compare(doc, out, s4) -> how our output compares with another S/4
                                  output (Databricks), row by row and field by field

Stored runs: data/outputs/<OBJECT>/<run_id>/S_<OBJECT>.csv + report.json.

Semantics follow the Databricks views where they are defined:
  * lookups are left joins; routing / fan-out copy rows per target;
  * ECC-side filters drop rows;
  * identical output rows are collapsed (SELECT DISTINCT);
  * a field whose inputs are missing is left blank and listed in
    `missing` (with the reason) instead of failing the run.
Comparison normalises formatting on both sides ('0.000' = '0', dates in
any of the usual formats), then applies the field's `compare` steps.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.core import paths
from app.core.jsonio import read_json, write_json_atomic
from app.inference.column_match import light_normalize
from app.mapping.model import MappingDoc
from app.transforms import engine

MAX_SAMPLES = 8


@dataclass
class GenResult:
    frame: pd.DataFrame
    missing: dict[str, str] = field(default_factory=dict)       # field id -> reason
    filters: list[dict] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    ecc_rows: int = 0
    rows_before_distinct: int = 0
    error: str | None = None


def generate(doc: MappingDoc, ecc_df: pd.DataFrame, refs: dict | None = None) -> GenResult:
    res = GenResult(frame=pd.DataFrame(), ecc_rows=int(len(ecc_df)))
    try:
        prepared = engine.prepare_ecc(doc, ecc_df, refs)
    except (KeyError, ValueError) as exc:
        res.error = f"could not prepare the ECC rows: {exc}"
        return res
    res.missing_inputs = engine.missing_inputs(prepared)
    filtered, res.filters = engine.apply_filters(prepared, doc, "ecc")
    filtered = filtered.reset_index(drop=True)
    filtered.attrs = prepared.attrs
    cols: dict[str, pd.Series] = {}
    for f in doc.fields:
        if not f.s4_column:
            continue
        try:
            cols[f.s4_column] = engine.expected_s4(doc, f, filtered).values
        except engine.MissingInput as exc:
            res.missing[f.id] = f"input missing: {str(exc).strip(chr(39))}"
            cols[f.s4_column] = pd.Series("", index=filtered.index)
        except (KeyError, ValueError) as exc:
            res.missing[f.id] = str(exc).strip("'")
            cols[f.s4_column] = pd.Series("", index=filtered.index)
    out = pd.DataFrame(cols, index=filtered.index)
    res.rows_before_distinct = int(len(out))
    out[engine.ECC_ROW_COL] = filtered[engine.ECC_ROW_COL].to_numpy()
    data_cols = [c for c in out.columns if c != engine.ECC_ROW_COL]
    out = out.drop_duplicates(subset=data_cols).reset_index(drop=True)
    res.frame = out
    return res


def _key_series(doc: MappingDoc, frame: pd.DataFrame) -> pd.Series:
    parts = []
    for f in doc.key_fields():
        vals = light_normalize(frame[f.s4_column]) if f.s4_column in frame.columns else np.full(len(frame), "")
        parts.append(engine.comparable(pd.Series(vals, index=frame.index), f, doc))
    out = parts[0]
    for p in parts[1:]:
        out = out + engine.KEY_SEP + p
    return out


def shadow_compare(doc: MappingDoc, ours: pd.DataFrame, theirs: pd.DataFrame,
                   missing: dict[str, str] | None = None) -> dict:
    """Row coverage and per-field agreement of `ours` against `theirs`."""
    missing = missing or {}
    ok = _key_series(doc, ours)
    tk = _key_series(doc, theirs)
    o_counts, t_counts = ok.value_counts(), tk.value_counts()
    o_set, t_set = set(ok), set(tk)
    both = o_set & t_set
    only_ours, only_theirs = sorted(o_set - t_set), sorted(t_set - o_set)
    key_names = [f.id for f in doc.key_fields()]

    def sample(keys):
        return [dict(zip(key_names, engine.split_key(k))) for k in keys[:MAX_SAMPLES]]

    rows = {
        "ours": int(len(ours)), "theirs": int(len(theirs)),
        "keys_both": len(both), "keys_only_ours": len(only_ours), "keys_only_theirs": len(only_theirs),
        "duplicate_keys_ours": int((o_counts > 1).sum()), "duplicate_keys_theirs": int((t_counts > 1).sum()),
        "theirs_reproduced_share": round(len(both) / len(t_set), 4) if t_set else None,
        "ours_confirmed_share": round(len(both) / len(o_set), 4) if o_set else None,
        "sample_only_ours": sample(only_ours), "sample_only_theirs": sample(only_theirs),
    }

    o_pos = pd.Series(np.arange(len(ok)), index=ok.to_numpy())[~ok.duplicated(keep=False).to_numpy()]
    t_pos = pd.Series(np.arange(len(tk)), index=tk.to_numpy())[~tk.duplicated(keep=False).to_numpy()]
    common = o_pos.index.intersection(t_pos.index)
    oi, ti = o_pos.loc[common].to_numpy(), t_pos.loc[common].to_numpy()

    fields = []
    for f in doc.fields:
        col = f.s4_column
        entry = {"field": f.id, "s4_column": col, "label": f.s4_label}
        if not col or col not in theirs.columns or col not in ours.columns:
            entry["status"] = "not_compared"
            entry["reason"] = "column missing on one side"
            fields.append(entry)
            continue
        if f.id in missing:
            entry["status"] = "input_missing" if "input missing" in missing[f.id] else "no_rule"
            entry["reason"] = missing[f.id]
            fields.append(entry)
            continue
        a = engine.comparable(ours[col], f, doc).to_numpy(dtype=str)[oi]
        b = engine.comparable(theirs[col], f, doc).to_numpy(dtype=str)[ti]
        informative = (a != "") | (b != "")
        n_inf = int(informative.sum())
        eq = a == b
        bad = np.where(~eq)[0]
        entry.update({
            "status": "match" if len(bad) == 0 else "mismatch",
            "compared": int(len(a)),
            "agreement": round(float(eq.mean()), 4) if len(a) else None,
            "agreement_non_blank": round(float(eq[informative].mean()), 4) if n_inf else None,
            "mismatches": int(len(bad)),
            "samples": [{"key": dict(zip(key_names, engine.split_key(common[i]))), "ours": str(a[i]),
                         "theirs": str(b[i])} for i in bad[:MAX_SAMPLES]],
        })
        fields.append(entry)
    compared = [x for x in fields if x.get("agreement") is not None]
    summary = {
        "fields_total": len(fields),
        "fields_matching": sum(1 for x in compared if x["mismatches"] == 0),
        "fields_mismatching": sum(1 for x in compared if x["mismatches"] > 0),
        "fields_input_missing": sum(1 for x in fields if x["status"] == "input_missing"),
        "fields_no_rule": sum(1 for x in fields if x["status"] == "no_rule"),
        "fields_not_compared": sum(1 for x in fields if x["status"] == "not_compared"),
        "cell_agreement": round(float(np.mean([x["agreement"] for x in compared])), 4) if compared else None,
        "keys_compared": int(len(common)),
    }
    fields.sort(key=lambda x: (x.get("agreement") if x.get("agreement") is not None else 2, x["field"]))
    return {"summary": summary, "rows": rows, "fields": fields}


# -- stored runs -----------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _input_versions(doc, obj: str) -> dict:
    """Versions of every input the run used: the object's ECC and S/4
    snapshots and the supporting tables its lookups read ("REF/<TABLE>";
    None when the table isn't delivered)."""
    from app.ingest.sources import OBJECTS, REF
    from app.store import snapshot_store as ss

    out = {side: ss.versions(obj, side)["current"] for side in ("ECC", "S4")}
    for table in sorted({lk.table.upper() for lk in doc.lookups}):
        if table == obj:
            continue
        if table in OBJECTS:                 # another object's ECC extract (MBEW joins MARC)
            out[f"{table}/ECC"] = ss.versions(table, "ECC")["current"]
        else:
            out[f"{REF}/{table}"] = ss.versions(REF, table)["current"]
    return out


def run(obj: str) -> dict:
    """Generate from the current snapshots + mapping, compare with the current
    S/4 (Databricks) snapshot, store both."""
    from app.mapping import repository
    from app.store import snapshot_store as ss

    doc = repository.load(obj)
    ecc = ss.read_combined(obj, "ECC")
    res = generate(doc, ecc)
    run_id = f"G-{obj}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"
    out_dir = paths.outputs_dir() / obj / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    s4_cols = [f.s4_column for f in doc.fields if f.s4_column]
    if not res.error:
        res.frame[[c for c in s4_cols if c in res.frame.columns]].to_csv(out_dir / f"S_{obj}.csv", index=False)
    report = {
        "run_id": run_id, "object": obj, "created_at": _now(), "mapping_version": doc.mapping_version,
        "snapshots": _input_versions(doc, obj),
        "error": res.error, "ecc_rows": res.ecc_rows, "rows_generated": int(len(res.frame)),
        "rows_before_distinct": res.rows_before_distinct, "missing_inputs": res.missing_inputs,
        "filters": res.filters, "fields_missing": res.missing,
    }
    if not res.error:
        try:
            s4 = ss.read_combined(obj, "S4")
            report["shadow"] = shadow_compare(doc, res.frame, s4, res.missing)
        except ss.SnapshotNotFound:
            report["shadow"] = None
    write_json_atomic(out_dir / "report.json", report)
    return report


def list_runs(obj: str | None = None, limit: int = 20) -> list[dict]:
    root = paths.outputs_dir()
    if not root.exists():
        return []
    dirs = [d for o in root.iterdir() if o.is_dir() and (not obj or o.name == obj) for d in o.iterdir() if d.is_dir()]
    out = []
    for d in sorted(dirs, key=lambda d: d.stat().st_mtime, reverse=True)[:limit]:
        r = read_json(d / "report.json") or {}
        out.append({k: r.get(k) for k in ("run_id", "object", "created_at", "mapping_version", "rows_generated",
                                          "error")} | {"summary": (r.get("shadow") or {}).get("summary"),
                                                       "rows": {k: v for k, v in ((r.get("shadow") or {})
                                                                .get("rows") or {}).items() if "sample" not in k}})
    return out


def get_run(obj: str, run_id: str) -> dict | None:
    return read_json(paths.outputs_dir() / obj / run_id / "report.json")


def output_path(obj: str, run_id: str):
    return paths.outputs_dir() / obj / run_id / f"S_{obj}.csv"
