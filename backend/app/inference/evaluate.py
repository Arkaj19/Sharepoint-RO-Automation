"""
Dry runs: what would a change to the mapping do to the data? Used by
Agent 1's tools and shown as evidence on proposal operations.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.inference.keys import Alignment, align
from app.mapping.model import FilterSpec, MappingDoc
from app.transforms import engine


def field_agreement(doc: MappingDoc, al: Alignment, field_id: str, sample: int = 5) -> dict:
    """Share of aligned pairs where the field's expected S/4 value (ECC value
    after transform + compare) equals the S/4 value (after compare)."""
    f = doc.field(field_id)
    if f is None:
        return {"error": f"unknown field '{field_id}'"}
    if not f.s4_column or f.s4_column not in al.s4.columns:
        return {"error": f"field '{field_id}' has no S/4 column"}
    if not doc.has_source(f) and not doc.is_derived_only(f) and field_id not in doc.fanout_fields() \
            and field_id not in doc.routed_fields():
        return {"error": f"field '{field_id}' has no ECC column"}
    try:
        exp = engine.expected_s4(doc, f, engine.prepare_ecc(doc, al.ecc))
    except (KeyError, ValueError) as exc:
        return {"error": str(exc)}
    e = engine.comparable(exp.values, f, doc).to_numpy(dtype=str)[al.pairs["ecc_pos"].to_numpy(dtype=int)]
    s = engine.comparable(al.s4[f.s4_column], f, doc).to_numpy(dtype=str)[al.pairs["s4_pos"].to_numpy(dtype=int)]
    informative = (e != "") | (s != "")
    support = int(informative.sum())
    if support == 0:
        return {"agreement": None, "support": 0, "pairs": int(len(e))}
    eq = e == s
    bad = np.where(informative & ~eq)[0][:sample]
    return {
        "agreement": round(float(eq[informative].mean()), 4),
        "support": support,
        "pairs": int(len(e)),
        "mismatch_samples": [{"expected": str(e[i]), "s4": str(s[i])} for i in bad],
    }


def evaluate_field_change(base_doc: MappingDoc, candidate_doc: MappingDoc, ecc_df: pd.DataFrame,
                          s4_df: pd.DataFrame, field_id: str, base_alignment: Alignment | None = None) -> dict:
    """Agreement of one field before and after a change. Key-field changes
    re-align the data (the pairs themselves change)."""
    is_key = field_id in candidate_doc.key.fields or field_id in base_doc.key.fields
    base_al = base_alignment or align(base_doc, ecc_df, s4_df)
    cand_al = align(candidate_doc, ecc_df, s4_df) if is_key else base_al
    before = field_agreement(base_al.doc if is_key else base_doc, base_al, field_id)
    after = field_agreement(cand_al.doc if is_key else candidate_doc, cand_al, field_id)
    out = {"field_id": field_id, "before": before, "after": after}
    if is_key:
        out["alignment_before"] = base_al.stats
        out["alignment_after"] = cand_al.stats
    return out


def expected_row_count(doc: MappingDoc, ecc_df: pd.DataFrame, s4_df: pd.DataFrame) -> dict:
    """ECC rows after fan-out and ECC filters, versus S/4 rows after S/4 filters."""
    ecc = engine.prepare_ecc(doc, ecc_df)
    ecc_f, ecc_report = engine.apply_filters(ecc, doc, "ecc")
    s4_f, s4_report = engine.apply_filters(s4_df, doc, "s4")
    return {
        "ecc_rows": int(len(ecc_df)), "ecc_rows_after_fanout": int(len(ecc)),
        "ecc_rows_expected": int(len(ecc_f)), "s4_rows": int(len(s4_df)), "s4_rows_after_filters": int(len(s4_f)),
        "gap": int(len(s4_f) - len(ecc_f)), "filters": ecc_report + s4_report,
    }


def evaluate_filter(doc: MappingDoc, flt: FilterSpec, ecc_df: pd.DataFrame, s4_df: pd.DataFrame) -> dict:
    before = expected_row_count(doc, ecc_df, s4_df)
    cand = doc.model_copy(deep=True)
    cand.filters = [f for f in cand.filters if f.id != flt.id] + [flt]
    try:
        after = expected_row_count(cand, ecc_df, s4_df)
    except (KeyError, ValueError) as exc:
        return {"error": str(exc)}
    return {"before": before, "after": after}
