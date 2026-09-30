"""
Deterministic diff of two versions of the same dataset (previous -> current):

  files    added / removed / changed part-files (by name and sha256)
  schema   added / removed columns, rename candidates, reordering
  profile  row count, blank rate, distinct count, new / removed code values,
           type drift, longer values
  keys     added / removed business keys, duplicates, and per-column counts
           of cells that changed for keys present in both versions

Every change is a plain dict {id, kind, side, severity, ...}; changeset.py
adds the mapping impact and ranks them.
"""
from __future__ import annotations

import difflib

import numpy as np
import pandas as pd

from app.inference.keys import norm_label
from app.ingest.readers import data_columns

BLANK_RATE_DELTA = 0.05
DISTINCT_REL_DELTA = 0.10
RENAME_MIN_SCORE = 0.6
RENAME_STRONG = 0.85
MAX_VALUES = 20
MAX_SAMPLES = 5


class _Ids:
    def __init__(self, prefix: str) -> None:
        self.prefix, self.n = prefix, 0

    def __call__(self) -> str:
        self.n += 1
        return f"{self.prefix}{self.n}"


def diff_files(prev_manifest: dict, cur_manifest: dict, side: str, next_id) -> list[dict]:
    prev = {f["name"]: f for f in prev_manifest.get("files", [])}
    cur = {f["name"]: f for f in cur_manifest.get("files", [])}
    out = []
    for name in sorted(cur.keys() - prev.keys()):
        out.append({"id": next_id(), "kind": "file_added", "side": side, "file": name,
                    "rows": cur[name].get("rows"), "severity": "medium"})
    for name in sorted(prev.keys() - cur.keys()):
        out.append({"id": next_id(), "kind": "file_removed", "side": side, "file": name,
                    "rows": prev[name].get("rows"), "severity": "high"})
    for name in sorted(prev.keys() & cur.keys()):
        if prev[name].get("sha256") != cur[name].get("sha256"):
            out.append({"id": next_id(), "kind": "file_changed", "side": side, "file": name,
                        "rows_before": prev[name].get("rows"), "rows_after": cur[name].get("rows"),
                        "severity": "low"})
    return out


def _value_sim(pa: dict, pb: dict) -> float:
    va = set((pa.get("values") or pa.get("top_values") or {}).keys())
    vb = set((pb.get("values") or pb.get("top_values") or {}).keys())
    if not va and not vb:
        return 1.0 if pa.get("blank_rate") == 1.0 and pb.get("blank_rate") == 1.0 else 0.0
    return len(va & vb) / len(va | vb) if va | vb else 0.0


def _profile_sim(pa: dict, pb: dict) -> float:
    s = 0.0
    s += 0.4 if pa.get("type") == pb.get("type") else 0.0
    s += 0.3 * (1 - min(abs(pa.get("blank_rate", 0) - pb.get("blank_rate", 0)) * 4, 1))
    la, lb = pa.get("max_len", 0), pb.get("max_len", 0)
    s += 0.3 * (1 - min(abs(la - lb) / max(la, lb, 1), 1))
    return s


def rename_candidates(removed: list[str], added: list[str], prev_prof: dict, cur_prof: dict) -> list[dict]:
    scored = []
    for r in removed:
        for a in added:
            name = difflib.SequenceMatcher(None, norm_label(r), norm_label(a)).ratio()
            pa, pb = prev_prof.get(r, {}), cur_prof.get(a, {})
            sc = 0.35 * name + 0.45 * _value_sim(pa, pb) + 0.20 * _profile_sim(pa, pb)
            if sc >= RENAME_MIN_SCORE:
                scored.append((round(sc, 3), r, a, round(name, 3)))
    scored.sort(reverse=True)
    used_r, used_a, out = set(), set(), []
    for sc, r, a, name in scored:
        if r in used_r or a in used_a:
            continue
        used_r.add(r)
        used_a.add(a)
        out.append({"from": r, "to": a, "score": sc, "name_similarity": name, "strong": sc >= RENAME_STRONG})
    return out


def diff_schema(prev_prof: dict, cur_prof: dict, side: str, next_id) -> list[dict]:
    pcols, ccols = prev_prof["columns"], cur_prof["columns"]
    removed = [c for c in pcols if c not in set(ccols)]
    added = [c for c in ccols if c not in set(pcols)]
    renames = rename_candidates(removed, added, prev_prof["profiles"], cur_prof["profiles"])
    renamed_from = {r["from"] for r in renames}
    renamed_to = {r["to"] for r in renames}
    out = []
    for r in renames:
        out.append({"id": next_id(), "kind": "column_renamed", "side": side, "from": r["from"], "to": r["to"],
                    "score": r["score"], "strong": r["strong"], "severity": "high"})
    for c in removed:
        if c not in renamed_from:
            out.append({"id": next_id(), "kind": "column_removed", "side": side, "column": c, "severity": "high"})
    for c in added:
        if c not in renamed_to:
            prof = cur_prof["profiles"].get(c, {})
            out.append({"id": next_id(), "kind": "column_added", "side": side, "column": c,
                        "blank_rate": prof.get("blank_rate"), "type": prof.get("type"), "severity": "medium"})
    common_prev = [c for c in pcols if c in set(ccols)]
    common_cur = [c for c in ccols if c in set(pcols)]
    if common_prev != common_cur:
        moved = [c for i, c in enumerate(common_cur) if i >= len(common_prev) or common_prev[i] != c]
        out.append({"id": next_id(), "kind": "columns_reordered", "side": side, "moved": moved[:MAX_VALUES],
                    "moved_count": len(moved), "severity": "low"})
    return out


def diff_profiles(prev_prof: dict, cur_prof: dict, side: str, next_id) -> list[dict]:
    out = []
    if prev_prof["rows"] != cur_prof["rows"]:
        out.append({"id": next_id(), "kind": "row_count_changed", "side": side, "before": prev_prof["rows"],
                    "after": cur_prof["rows"], "delta": cur_prof["rows"] - prev_prof["rows"], "severity": "medium"})
    for col in cur_prof["columns"]:
        pa, pb = prev_prof["profiles"].get(col), cur_prof["profiles"].get(col)
        if not pa or not pb:
            continue
        if abs(pb["blank_rate"] - pa["blank_rate"]) > BLANK_RATE_DELTA:
            out.append({"id": next_id(), "kind": "blank_rate_changed", "side": side, "column": col,
                        "before": pa["blank_rate"], "after": pb["blank_rate"], "severity": "medium"})
        if pa.get("is_code") or pb.get("is_code"):
            va, vb = pa.get("values") or {}, pb.get("values") or {}
            if pa.get("is_code") and pb.get("is_code"):
                new = {k: v for k, v in vb.items() if k not in va}
                gone = {k: v for k, v in va.items() if k not in vb}
                if new:
                    out.append({"id": next_id(), "kind": "new_code_values", "side": side, "column": col,
                                "values": dict(list(new.items())[:MAX_VALUES]), "severity": "high"})
                if gone:
                    out.append({"id": next_id(), "kind": "removed_code_values", "side": side, "column": col,
                                "values": dict(list(gone.items())[:MAX_VALUES]), "severity": "medium"})
            elif pa.get("is_code") != pb.get("is_code"):
                out.append({"id": next_id(), "kind": "distinct_changed", "side": side, "column": col,
                            "before": pa["distinct"], "after": pb["distinct"], "severity": "medium"})
        elif pa["distinct"] and abs(pb["distinct"] - pa["distinct"]) / pa["distinct"] > DISTINCT_REL_DELTA \
                and abs(pb["distinct"] - pa["distinct"]) > 5:
            out.append({"id": next_id(), "kind": "distinct_changed", "side": side, "column": col,
                        "before": pa["distinct"], "after": pb["distinct"], "severity": "low"})
        if pa["type"] != pb["type"] and "empty" not in (pa["type"], pb["type"]):
            out.append({"id": next_id(), "kind": "type_changed", "side": side, "column": col,
                        "before": pa["type"], "after": pb["type"], "severity": "medium"})
        if pb["max_len"] > pa["max_len"]:
            out.append({"id": next_id(), "kind": "length_increased", "side": side, "column": col,
                        "before": pa["max_len"], "after": pb["max_len"], "severity": "low"})
    return out


def _key(df: pd.DataFrame, cols: list[str], normalizers: list) -> pd.Series:
    parts = []
    for c, norm in zip(cols, normalizers):
        v = df[c].fillna("").astype(str).str.strip()
        parts.append(norm(v) if norm else v)
    out = parts[0]
    for p in parts[1:]:
        out = out + "|" + p
    return out


def diff_keys(prev_df: pd.DataFrame, cur_df: pd.DataFrame, key_cols: list[str], normalizers: list,
              side: str, next_id, max_cols: int = 30) -> list[dict]:
    if not key_cols or any(c not in prev_df.columns or c not in cur_df.columns for c in key_cols):
        return []
    pk, ck = _key(prev_df, key_cols, normalizers), _key(cur_df, key_cols, normalizers)
    out = []
    pset, cset = set(pk), set(ck)
    added, removed = sorted(cset - pset), sorted(pset - cset)
    if added:
        out.append({"id": next_id(), "kind": "keys_added", "side": side, "key_columns": key_cols,
                    "count": len(added), "samples": added[:MAX_SAMPLES], "severity": "medium"})
    if removed:
        out.append({"id": next_id(), "kind": "keys_removed", "side": side, "key_columns": key_cols,
                    "count": len(removed), "samples": removed[:MAX_SAMPLES], "severity": "medium"})
    dup_prev, dup_cur = int(pk.duplicated().sum()), int(ck.duplicated().sum())
    if dup_cur and dup_cur != dup_prev:
        out.append({"id": next_id(), "kind": "duplicate_keys", "side": side, "before": dup_prev,
                    "after": dup_cur, "severity": "medium"})

    p_unique = prev_df[~pk.duplicated(keep=False)].set_index(pk[~pk.duplicated(keep=False)])
    c_unique = cur_df[~ck.duplicated(keep=False)].set_index(ck[~ck.duplicated(keep=False)])
    common = p_unique.index.intersection(c_unique.index)
    if len(common):
        cols = [c for c in data_columns(cur_df) if c in prev_df.columns and c not in key_cols]
        a = p_unique.loc[common, cols].astype(str).to_numpy()
        b = c_unique.loc[common, cols].astype(str).to_numpy()
        changed = (a != b)
        counts = changed.sum(axis=0)
        order = np.argsort(-counts)
        changed_cols = []
        for j in order[:max_cols]:
            if counts[j] == 0:
                break
            rows = np.where(changed[:, j])[0][:3]
            changed_cols.append({"column": cols[j], "changed": int(counts[j]),
                                 "samples": [{"key": str(common[i]), "before": a[i, j], "after": b[i, j]}
                                             for i in rows]})
        if changed_cols:
            out.append({"id": next_id(), "kind": "cells_changed", "side": side, "keys_compared": int(len(common)),
                        "columns_changed": int((counts > 0).sum()), "columns": changed_cols, "severity": "high"})
    return out
