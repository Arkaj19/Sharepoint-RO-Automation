"""
Matches ECC columns (SAP screen labels) to mapped S/4 fields (technical
names) using two kinds of evidence:

  * value agreement on aligned row pairs, after light normalisation
    (strip, canonical numbers so '1.0' == '1', canonical dates);
  * label similarity between the field's S/4 label and the ECC header.

Candidate pairs are pre-filtered by shared distinct values, so the full
146 x 249 comparison stays fast.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.ingest.readers import data_columns
from app.inference.keys import Alignment, name_similarity
from app.mapping.model import MappingDoc
from app.transforms import engine


MIN_SUPPORT = 20


def light_normalize(series: pd.Series) -> np.ndarray:
    """Formatting-insensitive values (see engine.normalize_value)."""
    return engine.normalize_series(series).to_numpy(dtype=str)


def _aligned(al: Alignment, side: str, col: str, cache: dict) -> np.ndarray:
    key = (side, col)
    if key not in cache:
        frame, pos_col = (al.ecc, "ecc_pos") if side == "ecc" else (al.s4, "s4_pos")
        cache[key] = light_normalize(frame[col])[al.pairs[pos_col].to_numpy(dtype=int)]
    return cache[key]


def agreement(e: np.ndarray, s: np.ndarray) -> tuple[float | None, int]:
    informative = (e != "") | (s != "")
    support = int(informative.sum())
    if support == 0:
        return None, 0
    return round(float((e[informative] == s[informative]).mean()), 4), support


def score(agree: float | None, support: int, name_sim: float) -> float:
    if agree is None or support < MIN_SUPPORT:
        return round(0.5 * name_sim, 4)
    return round(0.75 * agree + 0.25 * name_sim, 4)


def match_fields(al: Alignment, doc: MappingDoc, field_ids: list[str] | None = None,
                 top_k: int = 5, exclude_columns: set[str] | None = None) -> dict[str, list[dict]]:
    """{field_id: [candidate, ...]} best first. A candidate is
    {column, agreement, support, name_similarity, score}."""
    cache: dict = {}
    exclude = exclude_columns or set()
    ecc_cols = [c for c in data_columns(al.ecc) if c not in exclude]
    have_pairs = len(al.pairs) > 0
    ecc_sets = {}
    if have_pairs:
        for c in ecc_cols:
            arr = _aligned(al, "ecc", c, cache)
            ecc_sets[c] = set(np.unique(arr[arr != ""])[:5000])

    out: dict[str, list[dict]] = {}
    for f in doc.fields:
        if field_ids is not None and f.id not in field_ids:
            continue
        if not f.s4_column or f.s4_column not in al.s4.columns:
            out[f.id] = []
            continue
        s_arr = _aligned(al, "s4", f.s4_column, cache) if have_pairs else None
        s_set = set(np.unique(s_arr[s_arr != ""])[:5000]) if s_arr is not None else set()
        cands = []
        for c in ecc_cols:
            ns = name_similarity(f.s4_label, c)
            agree, support = None, 0
            if have_pairs and (s_set & ecc_sets[c] or (not s_set and ns >= 0.6)):
                agree, support = agreement(_aligned(al, "ecc", c, cache), s_arr)
            elif ns < 0.6:
                continue
            cands.append({"column": c, "agreement": agree, "support": support,
                          "name_similarity": ns, "score": score(agree, support, ns)})
        cands.sort(key=lambda r: (-r["score"], -r["support"]))
        out[f.id] = cands[:top_k]
    return out


def assign(matches: dict[str, list[dict]]) -> dict[str, dict]:
    """Greedy one-to-one assignment: highest-scoring (field, column) pairs first."""
    flat = [(c["score"], fid, c) for fid, cands in matches.items() for c in cands]
    flat.sort(key=lambda t: -t[0])
    taken_cols, taken_fields, out = set(), set(), {}
    for sc, fid, c in flat:
        if fid in taken_fields or c["column"] in taken_cols:
            continue
        out[fid] = c
        taken_fields.add(fid)
        taken_cols.add(c["column"])
    return out
