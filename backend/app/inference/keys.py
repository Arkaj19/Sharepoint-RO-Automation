"""
Business-key discovery and ECC <-> S/4 row alignment.

Most inference needs ECC and S/4 rows paired up. The key is normally
material + plant, but at bootstrap the mapping knows neither the ECC
columns nor how the values are re-coded, so alignment works it out:

  1. Primary key (material): the ECC column and normalisation with the
     biggest overlap of distinct values with the S/4 key column.
  2. Secondary key (plant / valuation area): the ECC column, and a
     crosswalk to the S/4 codes. If the mapping already has one it is
     used; otherwise one is inferred (see value_maps.infer_crosswalk) and
     used provisionally - it is reported, never silently applied.
  3. Rows are joined on the full key; only keys that are unique on both
     sides are kept as pairs.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import pandas as pd

from app.ingest.readers import data_columns
from app.mapping.model import (
    MappingDoc, Provenance, StripLeadingZerosStep, ValueMap, ValueMapEntry, ValueMapStep,
)
from app.transforms import engine

INFERRED_MAP_ID = "__inferred_crosswalk"

NORMALIZATIONS = {
    "identity": [],
    "strip_leading_zeros": [StripLeadingZerosStep(op="strip_leading_zeros")],
}


def norm_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def name_similarity(a: str, b: str) -> float:
    na, nb = norm_label(a), norm_label(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return round(difflib.SequenceMatcher(None, na, nb).ratio(), 3)


def _distinct(series: pd.Series, steps: list, doc: MappingDoc) -> set[str]:
    vals = engine.apply_steps(series, steps, doc=doc).values.str.strip()
    return set(vals[vals != ""].unique())


def test_key_normalization(ecc_values: pd.Series, s4_values: pd.Series, doc: MappingDoc) -> list[dict]:
    """Overlap of distinct values under each normalisation (applied to both sides)."""
    out = []
    for name, steps in NORMALIZATIONS.items():
        e, s = _distinct(ecc_values, steps, doc), _distinct(s4_values, steps, doc)
        inter = len(e & s)
        out.append({"normalization": name, "overlap": inter, "ecc_distinct": len(e), "s4_distinct": len(s),
                    "share_of_s4": round(inter / len(s), 4) if s else 0.0})
    return sorted(out, key=lambda r: -r["overlap"])


def find_key_column(doc: MappingDoc, field_id: str, ecc_df: pd.DataFrame, s4_df: pd.DataFrame,
                    top_k: int = 3) -> list[dict]:
    """Ranks ECC columns by how well their values overlap the S/4 key column."""
    f = doc.field(field_id)
    if f is None or not f.s4_column or f.s4_column not in s4_df.columns:
        return []
    s4_sets = {n: _distinct(s4_df[f.s4_column], steps, doc) for n, steps in NORMALIZATIONS.items()}
    s4_size = max((len(s) for s in s4_sets.values()), default=0)
    ranked = []
    for col in data_columns(ecc_df):
        raw = engine.as_text(ecc_df[col]).str.strip()
        if raw[raw != ""].nunique() < max(10, 0.05 * s4_size):
            continue
        for n, steps in NORMALIZATIONS.items():
            e = _distinct(ecc_df[col], steps, doc)
            inter = len(e & s4_sets[n])
            if inter:
                ranked.append({"column": col, "normalization": n, "overlap": inter,
                               "share_of_s4": round(inter / len(s4_sets[n]), 4) if s4_sets[n] else 0.0,
                               "name_similarity": name_similarity(f.s4_label, col)})
    ranked.sort(key=lambda r: (-r["overlap"], -r["name_similarity"]))
    seen, out = set(), []
    for r in ranked:
        if r["column"] in seen:
            continue
        seen.add(r["column"])
        out.append(r)
        if len(out) >= top_k:
            break
    return out


@dataclass
class Alignment:
    doc: MappingDoc                      # working copy used to align (may hold inferred key setup)
    ecc: pd.DataFrame                    # prepared ECC frame (fan-out expanded)
    s4: pd.DataFrame
    pairs: pd.DataFrame                  # columns: ecc_pos, s4_pos (positions in ecc / s4)
    method: str
    notes: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    key_setup: dict = field(default_factory=dict)


def _setup_primary(work: MappingDoc, ecc_df, s4_df, notes: list[str], setup: dict) -> bool:
    fid = work.key.fields[0]
    f = work.field(fid)
    if f is None or not f.s4_column or f.s4_column not in s4_df.columns:
        notes.append(f"primary key field '{fid}' has no S/4 column")
        return False
    if f.ecc_column and f.ecc_column in ecc_df.columns:
        if not f.compare and not f.transform:
            best = test_key_normalization(ecc_df[f.ecc_column], s4_df[f.s4_column], work)[0]
            if best["normalization"] != "identity":
                f.compare = list(NORMALIZATIONS[best["normalization"]])
                notes.append(f"'{fid}' compared with {best['normalization']} (not yet in the mapping)")
        setup[fid] = {"ecc_column": f.ecc_column, "source": "mapping"}
        return True
    cands = find_key_column(work, fid, ecc_df, s4_df, top_k=1)
    if not cands:
        notes.append(f"no ECC column overlaps the S/4 key column '{f.s4_column}'")
        return False
    best = cands[0]
    f.ecc_column = best["column"]
    f.compare = list(NORMALIZATIONS[best["normalization"]])
    setup[fid] = {"ecc_column": best["column"], "source": "inferred", **best}
    notes.append(f"'{fid}' ECC column inferred as '{best['column']}' ({best['normalization']}, "
                 f"{best['overlap']} shared values)")
    return True


def _setup_secondary(work: MappingDoc, ecc_df, s4_df, notes: list[str], setup: dict) -> None:
    from app.inference import value_maps  # local import: value_maps uses this module

    for fid in work.key.fields[1:]:
        f = work.field(fid)
        if f is None or not f.s4_column or f.s4_column not in s4_df.columns:
            notes.append(f"key field '{fid}' has no S/4 column")
            continue
        if not f.ecc_column or f.ecc_column not in ecc_df.columns:
            cands = [c for c in data_columns(ecc_df) if name_similarity(f.s4_label, c) == 1.0]
            if not cands:
                notes.append(f"no ECC column named like '{f.s4_label}' for key field '{fid}'")
                continue
            f.ecc_column = cands[0]
            setup[fid] = {"ecc_column": cands[0], "source": "inferred_by_name"}
        else:
            setup[fid] = {"ecc_column": f.ecc_column, "source": "mapping"}
        if f.transform:
            setup[fid]["crosswalk"] = "mapping"
            continue
        e_vals = set(engine.as_text(ecc_df[f.ecc_column]).str.strip().unique())
        s_vals = set(engine.as_text(s4_df[f.s4_column]).str.strip().unique())
        if len(e_vals & s_vals - {""}) >= 0.8 * max(len(s_vals - {""}), 1):
            setup[fid]["crosswalk"] = "identity"
            continue
        xw = value_maps.infer_crosswalk(work, fid, ecc_df, s4_df)
        entries = [ValueMapEntry(**{"from": e["from"], "to": e["to"]}, provenance=Provenance())
                   for e in xw["entries"] if e["accepted"]]
        if not entries:
            notes.append(f"could not infer a crosswalk for key field '{fid}'")
            continue
        map_id = f"{INFERRED_MAP_ID}_{fid}"
        work.value_maps[map_id] = ValueMap(on_missing="flag", entries=entries)
        f.transform = [ValueMapStep(op="value_map", map=map_id)]
        setup[fid]["crosswalk"] = f"inferred ({xw['method']})"
        notes.append(f"'{fid}' aligned with an inferred crosswalk ({len(entries)} entries, {xw['method']})")


def align(doc: MappingDoc, ecc_df: pd.DataFrame, s4_df: pd.DataFrame) -> Alignment:
    work = doc.model_copy(deep=True)
    for f in work.fields:                     # resolve dictionary-based sources once
        if not f.ecc_column:
            f.ecc_column = work.own_ecc_column_of(f)
    notes: list[str] = []
    setup: dict = {}
    s4 = s4_df.reset_index(drop=True)
    if not _setup_primary(work, ecc_df, s4, notes, setup):
        empty = pd.DataFrame(columns=["ecc_pos", "s4_pos"])
        return Alignment(work, engine.prepare_ecc(work, ecc_df), s4, empty, "none", notes)
    _setup_secondary(work, ecc_df, s4, notes, setup)
    # drop key fields we could not set up, so the remaining key still aligns
    usable = [k for k in work.key.fields
              if (work.field(k).ecc_column in ecc_df.columns) and (work.field(k).s4_column in s4.columns)]
    work.key.fields = usable
    ecc = engine.prepare_ecc(work, ecc_df)
    try:
        ek = engine.ecc_key_series(work, ecc)
        sk = engine.s4_key_series(work, s4)
    except (KeyError, ValueError) as exc:
        notes.append(f"key could not be built: {exc}")
        empty = pd.DataFrame(columns=["ecc_pos", "s4_pos"])
        return Alignment(work, ecc, s4, empty, "none", notes)

    e_counts, s_counts = ek.value_counts(), sk.value_counts()
    e_unique = pd.DataFrame({"key": ek, "ecc_pos": range(len(ek))})
    e_unique = e_unique[e_unique["key"].map(e_counts) == 1]
    s_unique = pd.DataFrame({"key": sk, "s4_pos": range(len(sk))})
    s_unique = s_unique[s_unique["key"].map(s_counts) == 1]
    pairs = e_unique.merge(s_unique, on="key")[["ecc_pos", "s4_pos"]]
    method = "full_key" if len(usable) == len(doc.key.fields) else "partial_key"
    stats = {
        "key_fields": usable,
        "ecc_rows": int(len(ecc)), "s4_rows": int(len(s4)),
        "ecc_duplicate_keys": int((e_counts > 1).sum()), "s4_duplicate_keys": int((s_counts > 1).sum()),
        "matched_pairs": int(len(pairs)),
        "s4_rows_matched_share": round(len(pairs) / len(s4), 4) if len(s4) else 0.0,
    }
    return Alignment(work, ecc, s4, pairs, method, notes, stats, setup)
