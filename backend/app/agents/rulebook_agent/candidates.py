"""
Deterministic proposer for Agent 1.

Turns measured evidence (alignment, column matching, crosswalk and split
inference, the ChangeSet) into candidate mapping operations with a
confidence computed from the metrics. It runs with or without a model:
with LLM_PROVIDER=none its output is the proposal; otherwise the model
reviews, adjusts, drops or adds to it.

Confidence guide
  >= 0.90  values agree on almost every aligned row (or lineage evidence)
  0.70-0.89 strong but not perfect evidence, or label-only on empty columns
  < 0.70   plausible, needs a human look
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.inference import column_match, value_maps
from app.inference.evaluate import expected_row_count
from app.inference.keys import INFERRED_MAP_ID, NORMALIZATIONS, Alignment, align
from app.mapping import ops as mops
from app.mapping.model import (
    CaseStep, CaseWhen, Condition, DateStep, FilterSpec, MappingDoc, NumberStep, ValueMapStep,
)
from app.transforms import engine

STRONG = 0.95
MEDIUM = 0.70
MIN_SUPPORT = column_match.MIN_SUPPORT
VALUE_MAP_PURITY = 0.98
SPLIT_PURITY = 0.98


@dataclass
class CandidateSet:
    ops: list = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    alignment: Alignment | None = None

    def add(self, op) -> str:
        op.op_id = f"c{len(self.ops) + 1:03d}"
        self.ops.append(op)
        return op.op_id


def _conf(x: float) -> float:
    return round(max(0.05, min(0.99, x)), 2)


def _raw_vs_light(al: Alignment, ecc_col: str, s4_col: str) -> tuple[float | None, float | None]:
    pv = value_maps.paired_values(al, ecc_col, s4_col)
    informative = (pv["ecc"] != "") | (pv["s4"] != "")
    if not informative.any():
        return None, None
    raw = float((pv["ecc"][informative] == pv["s4"][informative]).mean())
    e = column_match.light_normalize(pd.Series(pv["ecc"][informative].to_numpy()))
    s = column_match.light_normalize(pd.Series(pv["s4"][informative].to_numpy()))
    return round(raw, 4), round(float((e == s).mean()), 4)


# -- key --------------------------------------------------------------------------------

def _key_candidates(doc: MappingDoc, al: Alignment, cs: CandidateSet) -> dict[str, str]:
    """Aliases, normalisation and crosswalks for key fields. Returns
    {field_id: op_id} of the alias ops so other ops can depend on them."""
    alias_ops: dict[str, str] = {}
    for fid in doc.key.fields:
        real, work = doc.field(fid), al.doc.field(fid)
        setup = al.key_setup.get(fid, {})
        if not real or not work:
            continue
        if not doc.has_source(real) and work.ecc_column:
            if fid == doc.key.fields[0]:
                ev = {k: setup.get(k) for k in ("overlap", "share_of_s4", "normalization") if k in setup}
                conf = _conf(0.6 + 0.4 * setup.get("share_of_s4", 0))
                reason = (f"ECC column '{work.ecc_column}' shares {setup.get('overlap')} distinct values with "
                          f"S/4 '{real.s4_column}' after {setup.get('normalization')}")
            else:
                ev = {"match": "label"}
                conf, reason = 0.9, f"ECC column '{work.ecc_column}' has the same label as S/4 field '{real.s4_label}'"
            ev.update({"alignment": al.stats})
            alias_ops[fid] = cs.add(mops.SetColumnAlias(op="set_column_alias", field_id=fid, side="ecc",
                                                        column=work.ecc_column, confidence=conf,
                                                        reason=reason, evidence=ev))
        if work.compare and not real.compare and not real.transform:
            names = [n for n, st in NORMALIZATIONS.items() if [s.op for s in st] == [s.op for s in work.compare]]
            cs.add(mops.SetTransform(
                op="set_transform", field_id=fid, compare=list(work.compare), confidence=0.95,
                reason=f"Material numbers differ only by leading zeros between ECC and S/4 "
                       f"({names[0] if names else 'normalised'} comparison)",
                evidence={"overlap": setup.get("overlap"), "share_of_s4": setup.get("share_of_s4")},
                depends_on=[alias_ops[fid]] if fid in alias_ops else []))
        inferred = [m for m in al.doc.value_maps if m.startswith(INFERRED_MAP_ID) and m.endswith(fid)]
        if inferred and not real.transform:
            map_id = f"{fid}_crosswalk"
            xw = value_maps.infer_crosswalk(al.doc, fid, al.ecc, al.s4)
            accepted = [e for e in xw["entries"] if e["accepted"]]
            if not accepted:
                continue
            min_score = min(e["score"] for e in accepted)
            fan = xw["fan_out"]
            reason = (f"S/4 {real.s4_label} codes are re-coded from ECC. Inferred from "
                      f"{'the per-plant S/4 file split' if xw['method'] == 'lineage' else 'which ECC code covers the materials of each S/4 code'}"
                      f"; lowest coverage {min_score:.0%}.")
            if fan:
                reason += " Fan-out (one ECC row -> several S/4 rows): " + \
                    "; ".join(f"{k} -> {', '.join(v)}" for k, v in fan.items()) + "."
            vm_op = cs.add(mops.AddValueMapEntries(
                op="add_value_map_entries", map_id=map_id,
                description=f"ECC {real.s4_label} -> S/4 {real.s4_label}",
                entries=[mops.MapEntry(**{"from": e["from"], "to": e["to"]}) for e in accepted],
                confidence=_conf(min_score if xw["method"] == "lineage" else min_score - 0.03),
                reason=reason,
                evidence={"method": xw["method"],
                          "entries": [{k: e[k] for k in ("from", "to", "score", "support", "runner_up")}
                                      for e in xw["entries"]],
                          "unresolved_s4_codes": [e["to"] for e in xw["unresolved"]]},
                depends_on=[alias_ops[fid]] if fid in alias_ops else []))
            cs.add(mops.SetTransform(
                op="set_transform", field_id=fid, transform=[ValueMapStep(op="value_map", map=map_id)],
                confidence=0.9, reason=f"Translate ECC {real.s4_label} through '{map_id}'",
                evidence={"alignment": al.stats}, depends_on=[vm_op]))
            for e in xw["unresolved"]:
                cs.unresolved.append({"kind": "crosswalk", "field": fid, "s4_code": e["to"],
                                      "best_source": e["from"], "score": e["score"], "runner_up": e["runner_up"]})
    return alias_ops


# -- non-key fields ---------------------------------------------------------------------

def _condition_for(discriminator: str, value: str, col_to_field: dict[str, str]) -> Condition | None:
    if discriminator.startswith("__target__"):
        return Condition(field=discriminator[len("__target__"):], on="target", op="eq", value=value)
    fid = col_to_field.get(discriminator)
    return Condition(field=fid, op="eq", value=value) if fid else None


def _ecc_column_op(doc: MappingDoc, f, column: str, **kw):
    """Points a field at another ECC column. A field that reads through the
    dictionary (source_ref, no explicit ecc_column) gets its dictionary entry
    updated, so every rule that references that SAP field follows and no stale
    label is left behind; other fields get a column alias."""
    if not f.ecc_column and f.source_ref:
        prefix, _, fld = f.source_ref.partition(".")
        if prefix.upper() == doc.alias() and fld:
            kw["reason"] = f"{kw.get('reason', '')} (field {f.id} reads {f.source_ref})".strip()
            return mops.SetDictionary(op="set_dictionary", table=doc.alias(), entries={fld.upper(): column}, **kw)
    return mops.SetColumnAlias(op="set_column_alias", field_id=f.id, side="ecc", column=column, **kw)


def _field_candidates(doc: MappingDoc, al: Alignment, cs: CandidateSet, alias_ops: dict[str, str],
                      renamed: dict[tuple[str, str], dict]) -> None:
    key_ids = set(doc.key.fields)
    todo = [f.id for f in doc.fields
            if f.id not in key_ids and not doc.is_derived_only(f)
            and not (doc.ecc_column_of(f) and (doc.ecc_column_of(f) in al.ecc.columns or "." in (f.source_ref or "")
                                               and doc.lookup(f.source_ref.split(".")[0])))]
    assigned_cols = {c for f in doc.fields if (c := doc.own_ecc_column_of(f)) and c in al.ecc.columns}
    assigned_cols |= {al.doc.field(k).ecc_column for k in doc.key.fields if al.doc.field(k).ecc_column}

    # renamed ECC columns first (incremental): follow the rename
    for fid in list(todo):
        f = doc.field(fid)
        own = doc.own_ecc_column_of(f)
        r = renamed.get(("ECC", own)) if own else None
        if r:
            alias_ops[fid] = cs.add(_ecc_column_op(
                doc, f, r["to"], confidence=_conf(r["score"]),
                reason=f"ECC column '{r['from']}' was renamed to '{r['to']}'",
                evidence={"change_id": r["id"], "score": r["score"]}))
            todo.remove(fid)
            assigned_cols.add(r["to"])

    matches = column_match.match_fields(al, al.doc, field_ids=todo, exclude_columns=assigned_cols)
    chosen = column_match.assign(matches)
    col_to_field = {al.doc.field(k).ecc_column: k for k in doc.key.fields if al.doc.field(k).ecc_column}
    col_to_field.update({c: f.id for f in doc.fields if (c := doc.own_ecc_column_of(f))})
    col_to_field.update({c["column"]: fid for fid, c in chosen.items()})

    for fid in todo:
        f = doc.field(fid)
        c = chosen.get(fid)
        runners = [x for x in matches.get(fid, []) if not c or x["column"] != c["column"]][:3]
        if not c:
            cs.unresolved.append({"kind": "no_ecc_column", "field": fid, "s4_label": f.s4_label,
                                  "s4_column": f.s4_column, "source": f.ecc_source.model_dump(exclude_none=True)})
            continue
        agree, support, ns = c["agreement"], c["support"], c["name_similarity"]
        ev = {"agreement": agree, "support": support, "name_similarity": ns, "runners_up": runners}
        if agree is not None and support >= MIN_SUPPORT and agree >= STRONG:
            conf = _conf(0.55 + 0.4 * agree + 0.05 * ns)
            reason = f"Values agree on {agree:.1%} of {support} aligned rows"
        elif agree is not None and support >= MIN_SUPPORT and agree >= MEDIUM:
            conf = _conf(0.35 + 0.4 * agree + 0.1 * ns)
            reason = f"Values agree on {agree:.1%} of {support} aligned rows; the rest may need a rule"
        elif ns == 1.0 and (agree is None or support < MIN_SUPPORT):
            conf = 0.6
            reason = (f"Same label as S/4 field '{f.s4_label}'; the column is (almost) empty in the aligned "
                      f"data, so values cannot confirm it")
        else:
            cs.unresolved.append({"kind": "weak_match", "field": fid, "s4_label": f.s4_label,
                                  "best": c, "runners_up": runners})
            continue
        if ns == 1.0:
            reason += f"; label matches '{c['column']}'"
        alias_ops[fid] = cs.add(_ecc_column_op(doc, f, c["column"], confidence=conf, reason=reason, evidence=ev))
        if agree is None or support < MIN_SUPPORT:
            continue
        # even at 100% agreement after light normalisation, raw text may differ ('12' vs '12.0')
        _rule_candidates(doc, al, cs, f, c["column"], alias_ops[fid], col_to_field, alias_ops)


def _rule_candidates(doc: MappingDoc, al: Alignment, cs: CandidateSet, f, ecc_col: str, alias_op: str,
                     col_to_field: dict[str, str], alias_ops: dict[str, str]) -> None:
    """Explains the rows that disagree: number/date formatting, a value map, or a split rule."""
    raw, light = _raw_vs_light(al, ecc_col, f.s4_column)
    if raw is not None and light is not None and light - raw >= 0.02:
        step = DateStep(op="date") if f.data_type == "Date" else NumberStep(op="number")
        cs.add(mops.SetTransform(
            op="set_transform", field_id=f.id, compare=[step], confidence=_conf(0.5 + 0.45 * light),
            reason=f"Values differ only in formatting ('1.0' vs '1', date formats): exact agreement {raw:.1%}, "
                   f"after canonical {'date' if f.data_type == 'Date' else 'number'} {light:.1%}",
            evidence={"raw_agreement": raw, "normalized_agreement": light}, depends_on=[alias_op]))
        if light >= 0.98:
            return  # formatting explains (almost) everything; don't invent value rules on raw text
    vm = value_maps.infer_value_map(al, ecc_col, f.s4_column)
    if not vm["entries"] or vm["distinct_ecc"] > 50:
        return
    changed = [e for e in vm["entries"] if e["from"] != e["to"] and e["support"] >= 3]
    impure = [e for e in vm["entries"] if e["purity"] < VALUE_MAP_PURITY and e["support"] >= MIN_SUPPORT]
    if impure:
        splits = value_maps.find_split(al, ecc_col, f.s4_column)
        best = splits[0] if splits else None
        if best and best["purity"] >= SPLIT_PURITY:
            cases, deps = [], [alias_op]
            for r in best["rules"]:
                if r["then"] == r["src"]:
                    continue
                cond = _condition_for(best["discriminator"], str(r["when"]), col_to_field)
                if cond is None:
                    break
                cases.append(CaseWhen(when=Condition(all=[Condition(field=f.id, op="eq", value=str(r["src"])), cond]),
                                      value=str(r["then"])))
            else:
                disc = best["discriminator"]
                disc_field = disc[len("__target__"):] if disc.startswith("__target__") else col_to_field.get(disc)
                if disc_field in alias_ops:
                    deps.append(alias_ops[disc_field])
                if disc.startswith("__target__"):
                    deps += [o.op_id for o in cs.ops if o.op == "set_transform" and o.field_id == disc_field
                             and o.transform]
                if cases:
                    cs.add(mops.AddSplitRule(
                        op="add_split_rule", field_id=f.id, case=CaseStep(op="case", cases=cases),
                        confidence=_conf(best["purity"] - 0.05),
                        reason=f"ECC {f.s4_label} becomes a different S/4 value depending on "
                               f"{best['discriminator'].replace('__target__', 'the target ') }: "
                               f"{len(cases)} exception rule(s), {best['purity']:.1%} of {best['rows']} rows explained",
                        evidence={"discriminator": best["discriminator"], "purity": best["purity"],
                                  "rows": best["rows"], "rules": best["rules"], "value_map": vm["entries"][:10]},
                        depends_on=deps))
                    return
        cs.unresolved.append({"kind": "impure_values", "field": f.id, "ecc_column": ecc_col,
                              "entries": impure[:5], "best_split": best})
        return
    if changed and vm["identity_share"] is not None and vm["identity_share"] < 0.98:
        map_id = f"{f.id}_map"
        cs.add(mops.AddValueMapEntries(
            op="add_value_map_entries", map_id=map_id, on_missing="passthrough",
            description=f"ECC {f.s4_label} -> S/4 {f.s4_label}",
            entries=[mops.MapEntry(**{"from": e["from"], "to": e["to"]}) for e in changed],
            confidence=_conf(min(e["purity"] for e in changed) - 0.05),
            reason=f"{len(changed)} ECC value(s) are consistently re-coded in S/4",
            evidence={"entries": changed[:20], "identity_share": vm["identity_share"]}, depends_on=[alias_op]))
        cs.add(mops.SetTransform(
            op="set_transform", field_id=f.id, transform=[ValueMapStep(op="value_map", map=map_id)],
            confidence=0.85, reason=f"Translate ECC values through '{map_id}'",
            evidence={}, depends_on=[cs.ops[-1].op_id]))


# -- other ----------------------------------------------------------------------------------

def _ignore_candidates(doc: MappingDoc, al: Alignment, cs: CandidateSet) -> None:
    used = {f.s4_column for f in doc.fields if f.s4_column} | doc.ignored("s4")
    # columns an op in this proposal already maps (e.g. the target of an S/4 rename) are not "unused"
    used |= {o.column for o in cs.ops if o.op == "set_column_alias" and o.side == "s4"}
    for col in [c for c in al.s4.columns if not str(c).startswith("__") and c not in used]:
        filled = int((engine.as_text(al.s4[col]).str.strip() != "").sum())
        cs.add(mops.IgnoreColumn(op="ignore_column", side="s4", column=col, confidence=0.6,
                                 reason=f"S/4 column '{col}' is not in the mapping ({filled} rows populated); "
                                        f"ignore it unless it should become a field",
                                 evidence={"populated_rows": filled}))


def _filter_candidates(doc: MappingDoc, al: Alignment, cs: CandidateSet) -> None:
    """Restrict ECC to the codes the crosswalk covers, when S/4 has none of the others."""
    for op in [o for o in cs.ops if o.op == "add_value_map_entries" and o.map_id.endswith("_crosswalk")]:
        fid = op.map_id[: -len("_crosswalk")]
        f = al.doc.field(fid)
        if not f or not f.ecc_column:
            continue
        sources = sorted({e.from_ for e in op.entries})
        outside = value_maps.ecc_sources_without_target(al.doc, fid, al.ecc.drop_duplicates(engine.ECC_ROW_COL)
                                                        if engine.ECC_ROW_COL in al.ecc.columns else al.ecc,
                                                        set(sources))
        if not outside:
            continue
        flt = FilterSpec(id=f"F-{doc.object}-{fid}-scope", side="ecc",
                         expr=Condition(field=fid, op="in", values=sources),
                         source_text="Derived: ECC codes with no S/4 counterpart are not migrated")
        trial = al.doc.model_copy(deep=True)
        trial.filters.append(flt)
        try:
            before = expected_row_count(al.doc, al.ecc, al.s4)
            after = expected_row_count(trial, al.ecc, al.s4)
        except (KeyError, ValueError):
            before = after = None
        cs.add(mops.AddFilter(
            op="add_filter", filter=flt, confidence=0.75,
            reason=f"ECC {f.s4_label} values {', '.join(list(outside)[:8])}{'...' if len(outside) > 8 else ''} "
                   f"({sum(outside.values())} rows) have no S/4 counterpart; keep only crosswalked codes",
            evidence={"ecc_rows_outside": outside, "row_counts_before": before, "row_counts_after": after},
            depends_on=[op.op_id]))


def _incremental_candidates(doc: MappingDoc, al: Alignment, cs: CandidateSet, changeset: dict) -> None:
    for ch in changeset.get("changes", []):
        if ch["kind"] == "column_renamed" and ch["side"] == "S4":
            fid = next((f.id for f in doc.fields if f.s4_column == ch["from"]), None)
            if fid:
                cs.add(mops.SetColumnAlias(op="set_column_alias", field_id=fid, side="s4", column=ch["to"],
                                           confidence=_conf(ch["score"]),
                                           reason=f"S/4 column '{ch['from']}' was renamed to '{ch['to']}'",
                                           evidence={"change_id": ch["id"], "score": ch["score"]}))
        elif ch["kind"] == "length_increased" and ch.get("exceeds_mapping_length"):
            fid = next((f.id for f in doc.fields if f.s4_column == ch["column"]), None)
            if fid:
                cs.add(mops.UpdateFieldAttrs(op="update_field_attrs", field_id=fid,
                                             set=mops.FieldAttrs(length=ch["after"]), confidence=0.6,
                                             reason=f"S/4 values are now up to {ch['after']} characters, "
                                                    f"longer than the mapped {ch['exceeds_mapping_length']}",
                                             evidence={"change_id": ch["id"]}))
    for fid, gap in (changeset.get("mapping_health", {}).get("crosswalk_gaps") or {}).items():
        f = doc.field(fid)
        vm = doc.value_maps.get(gap["value_map"])
        if not f or vm is None:
            continue
        xw = value_maps.infer_crosswalk(al.doc, fid, al.ecc, al.s4)
        known = {(e.from_, e.to) for e in vm.entries}
        new = [e for e in xw["entries"] if e["accepted"] and (e["from"], e["to"]) not in known
               and e["from"] in gap["unmapped_values"]]
        if new:
            cs.add(mops.AddValueMapEntries(
                op="add_value_map_entries", map_id=gap["value_map"],
                entries=[mops.MapEntry(**{"from": e["from"], "to": e["to"]}) for e in new],
                confidence=_conf(min(e["score"] for e in new) - 0.03),
                reason=f"New ECC {f.s4_label} code(s) {', '.join(e['from'] for e in new)} now appear and map to "
                       f"{', '.join(e['to'] for e in new)}", evidence={"entries": new, "method": xw["method"]}))
        for code, rows in gap["unmapped_values"].items():
            if not any(e["from"] == code for e in new):
                cs.unresolved.append({"kind": "crosswalk_gap", "field": fid, "ecc_code": code, "rows": rows})


def build(doc: MappingDoc, ecc_df: pd.DataFrame, s4_df: pd.DataFrame, changeset: dict) -> CandidateSet:
    cs = CandidateSet()
    al = align(doc, ecc_df, s4_df)
    cs.alignment = al
    cs.notes += al.notes
    renamed = {(c["side"], c["from"]): c for c in changeset.get("changes", []) if c["kind"] == "column_renamed"}
    alias_ops = _key_candidates(doc, al, cs)
    _field_candidates(doc, al, cs, alias_ops, renamed)
    _incremental_candidates(doc, al, cs, changeset)     # before ignore: renames claim their new column
    _ignore_candidates(doc, al, cs)
    _filter_candidates(doc, al, cs)
    return cs


def summarize(cs: CandidateSet) -> str:
    counts: dict[str, int] = {}
    for op in cs.ops:
        counts[op.op] = counts.get(op.op, 0) + 1
    unresolved: dict[str, int] = {}
    for u in cs.unresolved:
        unresolved[u["kind"]] = unresolved.get(u["kind"], 0) + 1
    parts = [f"{len(cs.ops)} operation(s) proposed: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
             if cs.ops else "No mapping changes needed - the mapping still fits the data"]
    if unresolved:
        parts.append("Needs a human decision: " + ", ".join(f"{v} {k}" for k, v in sorted(unresolved.items())))
    return ". ".join(parts) + "."
