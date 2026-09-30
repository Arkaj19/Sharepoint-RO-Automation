"""
Crosswalk (value map) and split-rule inference.

infer_crosswalk      - for a key field such as plant, whose codes are re-coded
                       between systems (ECC 1021 -> S/4 US27 / US30). Evidence,
                       strongest first:
                         * lineage: the S/4 extract is split per source plant
                           (__src_part), so each S/4 row names its ECC plant;
                         * coverage: for each S/4 code, the share of its
                           materials that also exist under each ECC code. The
                           true source covers ~100%; one ECC code covering several
                           S/4 codes is a fan-out.
infer_value_map      - for non-key fields, on aligned row pairs: per ECC value,
                       the S/4 value it becomes, with purity and support.
find_split           - for ECC values that become more than one S/4 value, the
                       column (incl. a fan-out target such as the S/4 plant)
                       that decides which one.
"""
from __future__ import annotations

import pandas as pd

from app.ingest.readers import SRC_PART_COL, data_columns
from app.mapping.model import MappingDoc, StripLeadingZerosStep
from app.transforms import engine

MIN_COVERAGE = 0.9
MIN_MARGIN = 0.1
MIN_SUPPORT = 20
SPLIT_MAX_DISTINCT = 12


def _primary_key_values(doc: MappingDoc, df: pd.DataFrame, side: str) -> pd.Series:
    f = doc.field(doc.key.fields[0])
    col = doc.own_ecc_column_of(f) if side == "ecc" else f.s4_column
    steps = f.compare or [StripLeadingZerosStep(op="strip_leading_zeros")]
    return engine.apply_steps(df[col], steps, doc=doc).values.str.strip()


def infer_crosswalk(doc: MappingDoc, field_id: str, ecc_df: pd.DataFrame, s4_df: pd.DataFrame) -> dict:
    """Needs the primary key's ECC column set in `doc` (see keys.align)."""
    f = doc.field(field_id)
    s4_codes = engine.as_text(s4_df[f.s4_column]).str.strip()

    if SRC_PART_COL in s4_df.columns and (engine.as_text(s4_df[SRC_PART_COL]) != "").any():
        src = engine.as_text(s4_df[SRC_PART_COL]).str.strip()
        ecol = doc.own_ecc_column_of(f)
        ecc_codes = set(engine.as_text(ecc_df[ecol]).str.strip().unique()) if ecol else set()
        if not ecc_codes or len(set(src.unique()) & ecc_codes) >= 0.5 * src.nunique():
            ct = pd.crosstab(s4_codes, src)
            entries = []
            for q, row in ct.iterrows():
                if q == "":
                    continue
                total = int(row.sum())
                top = row.sort_values(ascending=False)
                p, n = top.index[0], int(top.iloc[0])
                entries.append({"from": str(p), "to": str(q), "score": round(n / total, 4), "support": total,
                                "runner_up": None, "accepted": n / total >= MIN_COVERAGE})
            return _finish(entries, "lineage")

    ecc_codes = engine.as_text(ecc_df[doc.own_ecc_column_of(f)]).str.strip()
    ek = _primary_key_values(doc, ecc_df, "ecc")
    sk = _primary_key_values(doc, s4_df, "s4")
    ecc_sets = pd.DataFrame({"code": ecc_codes, "k": ek}).groupby("code")["k"].apply(set)
    s4_sets = pd.DataFrame({"code": s4_codes, "k": sk}).groupby("code")["k"].apply(set)
    entries = []
    for q, qset in s4_sets.items():
        if q == "" or not qset:
            continue
        scores = sorted(((len(qset & pset) / len(qset), p) for p, pset in ecc_sets.items() if p != ""),
                        reverse=True)
        if not scores:
            continue
        (best, p), runner = scores[0], (scores[1] if len(scores) > 1 else (0.0, None))
        entries.append({
            "from": str(p), "to": str(q), "score": round(best, 4), "support": len(qset),
            "runner_up": {"from": runner[1], "score": round(runner[0], 4)} if runner[1] else None,
            "accepted": best >= MIN_COVERAGE and best - runner[0] >= MIN_MARGIN,
        })
    return _finish(entries, "material_coverage")


def _finish(entries: list[dict], method: str) -> dict:
    entries.sort(key=lambda e: (e["from"], e["to"]))
    fanout: dict[str, list[str]] = {}
    for e in entries:
        if e["accepted"]:
            fanout.setdefault(e["from"], []).append(e["to"])
    return {
        "method": method,
        "entries": entries,
        "fan_out": {k: v for k, v in fanout.items() if len(v) > 1},
        "unresolved": [e for e in entries if not e["accepted"]],
    }


def ecc_sources_without_target(doc: MappingDoc, field_id: str, ecc_df: pd.DataFrame,
                               sources: set[str]) -> dict[str, int]:
    f = doc.field(field_id)
    codes = engine.as_text(ecc_df[doc.own_ecc_column_of(f)]).str.strip()
    counts = codes[~codes.isin(sources) & (codes != "")].value_counts()
    return {str(k): int(v) for k, v in counts.items()}


# -- non-key fields, on aligned pairs ------------------------------------------------

def paired_values(al, ecc_col: str, s4_col: str) -> pd.DataFrame:
    e = engine.as_text(al.ecc[ecc_col]).str.strip().to_numpy()[al.pairs["ecc_pos"].to_numpy(dtype=int)]
    s = engine.as_text(al.s4[s4_col]).str.strip().to_numpy()[al.pairs["s4_pos"].to_numpy(dtype=int)]
    return pd.DataFrame({"ecc": e, "s4": s})


def infer_value_map(al, ecc_col: str, s4_col: str, max_entries: int = 40, max_distinct: int = 200) -> dict:
    pv = paired_values(al, ecc_col, s4_col)
    pv = pv[(pv["ecc"] != "") | (pv["s4"] != "")]
    if pv.empty:
        return {"pairs": 0, "entries": [], "identity_share": None, "distinct_ecc": 0, "distinct_s4": 0}
    identity = float((pv["ecc"] == pv["s4"]).mean())
    n_ecc, n_s4 = int(pv["ecc"].nunique()), int(pv["s4"].nunique())
    if n_ecc > max_distinct or n_s4 > max_distinct:
        return {"pairs": int(len(pv)), "identity_share": round(identity, 4), "distinct_ecc": n_ecc,
                "distinct_s4": n_s4, "entries": [], "too_many_values": True}
    ct = pd.crosstab(pv["ecc"], pv["s4"])
    entries = []
    for src, row in ct.iterrows():
        total = int(row.sum())
        top = row.sort_values(ascending=False)
        entries.append({
            "from": str(src), "to": str(top.index[0]), "support": total,
            "purity": round(int(top.iloc[0]) / total, 4),
            "others": {str(k): int(v) for k, v in top.iloc[1:4].items() if v},
        })
    entries.sort(key=lambda e: -e["support"])
    return {"pairs": int(len(pv)), "identity_share": round(identity, 4),
            "distinct_ecc": int(ct.shape[0]), "distinct_s4": int(ct.shape[1]),
            "entries": entries[:max_entries], "truncated": len(entries) > max_entries}


def find_split(al, ecc_col: str, s4_col: str, source_value: str | None = None, top_k: int = 5) -> list[dict]:
    """For rows whose ECC value (optionally only `source_value`) maps to more
    than one S/4 value, rank candidate discriminator columns by how pure the
    (value, discriminator) -> S/4 value mapping becomes."""
    ecc_pos = al.pairs["ecc_pos"].to_numpy(dtype=int)
    pv = paired_values(al, ecc_col, s4_col)
    if source_value is not None:
        keep = (pv["ecc"] == source_value).to_numpy()
    else:
        impure = pv.groupby("ecc")["s4"].nunique()
        keep = pv["ecc"].isin(impure[impure > 1].index).to_numpy()
    if not keep.any():
        return []
    pv = pv[keep].reset_index(drop=True)
    positions = ecc_pos[keep]
    candidates = [c for c in data_columns(al.ecc) if c != ecc_col] + \
                 [c for c in al.ecc.columns if str(c).startswith("__target__")]
    results = []
    for c in candidates:
        disc = engine.as_text(al.ecc[c]).str.strip().to_numpy()[positions]
        if pd.Series(disc).nunique() > SPLIT_MAX_DISTINCT:
            continue
        df = pd.DataFrame({"src": pv["ecc"], "d": disc, "tgt": pv["s4"]})
        g = df.groupby(["src", "d"])["tgt"].agg(lambda s: s.value_counts().iloc[0])
        purity = float(g.sum() / len(df))
        rules = (df.groupby(["src", "d"])["tgt"].agg(lambda s: s.value_counts().index[0]).reset_index()
                 .rename(columns={"d": "when", "tgt": "then"}).head(12).to_dict(orient="records"))
        results.append({"discriminator": str(c), "purity": round(purity, 4), "rows": int(len(df)), "rules": rules})
    results.sort(key=lambda r: -r["purity"])
    return results[:top_k]
