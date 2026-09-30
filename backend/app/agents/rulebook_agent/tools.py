"""
Agent 1's tools. Read-only views over the mapping, the ChangeSet, the data
and the inference helpers, plus four tools that change the *draft
proposal* (never the mapping): add_ops, drop_candidates, adjust_candidates
and submit_proposal.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.agents.runtime.masking import Masker
from app.agents.runtime.tools import ToolInputError, ToolRegistry
from app.core.config import settings
from app.inference import column_match, keys as inf_keys, value_maps
from app.inference.evaluate import evaluate_field_change, evaluate_filter, field_agreement
from app.ingest.readers import data_columns
from app.mapping import ops as mops
from app.mapping import render
from app.mapping.model import FilterSpec, MappingDoc
from app.transforms import engine


@dataclass
class AgentState:
    doc: MappingDoc
    changeset: dict
    ecc: pd.DataFrame
    s4: pd.DataFrame
    alignment: inf_keys.Alignment
    ops: list = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    sample_rows_used: int = 0
    finished: bool = False
    summary: str | None = None
    masker: Masker | None = None
    profiles: dict = field(default_factory=dict)
    _added: int = 0

    def headers(self) -> dict[str, set[str]]:
        return {"ecc": set(data_columns(self.ecc)), "s4": set(data_columns(self.s4))}

    def op(self, op_id: str):
        return next((o for o in self.ops if o.op_id == op_id), None)


def _page(items: list, offset: int, limit: int) -> dict:
    limit = max(1, min(limit, 200))
    return {"total": len(items), "offset": offset, "limit": limit, "items": items[offset: offset + limit]}


def _op_brief(o) -> dict:
    d = {"id": o.op_id, "op": o.op, "gate": o.gate, "confidence": o.confidence, "reason": o.reason,
         "source": o.source}
    for attr in ("field_id", "map_id", "side", "column"):
        if hasattr(o, attr):
            d[attr] = getattr(o, attr)
    if o.op == "add_value_map_entries":
        d["entries"] = [f"{e.from_}->{e.to}" for e in o.entries][:30]
    if o.op == "add_filter":
        d["filter"] = render.filter_text(o.filter)
    if o.op in ("set_transform", "add_split_rule"):
        d["preview"] = (o.preview or {}).get("after")
    if o.depends_on:
        d["depends_on"] = o.depends_on
    return d


def build_registry(st: AgentState) -> ToolRegistry:
    reg = ToolRegistry()
    doc = st.doc
    al = st.alignment

    def _field(fid: str):
        f = doc.field(fid)
        if f is None:
            raise ToolInputError(f"unknown field '{fid}'")
        return f

    def _col(side: str, col: str) -> pd.DataFrame:
        frame = st.ecc if side == "ecc" else st.s4 if side == "s4" else None
        if frame is None:
            raise ToolInputError("side must be 'ecc' or 's4'")
        if col not in frame.columns:
            close = [c for c in data_columns(frame) if inf_keys.name_similarity(col, c) > 0.7][:5]
            raise ToolInputError(f"{side.upper()} column '{col}' not found; similar: {close}")
        return frame

    # -- mapping ----------------------------------------------------------------------
    def get_mapping_summary(filter: str = "all", offset: int = 0, limit: int = 60):
        fields = doc.fields
        if filter == "unmapped":
            fields = [f for f in fields if not doc.has_source(f) and not doc.is_derived_only(f)]
        elif filter == "mapped":
            fields = [f for f in fields if doc.has_source(f) or doc.is_derived_only(f)]
        elif filter == "key":
            fields = doc.key_fields()
        rows = [{k: v for k, v in render.field_summary(f).items() if v not in (None, "copy as is")} for f in fields]
        return {"object": doc.object, "mapping_version": doc.mapping_version, "key": doc.key.fields,
                "value_maps": {k: len(v.entries) for k, v in doc.value_maps.items()},
                "filters": [render.filter_text(x) for x in doc.filters], **_page(rows, offset, limit)}

    reg.register("get_mapping_summary", "Compact table of the current mapping's fields.", {"properties": {
        "filter": {"type": "string", "enum": ["all", "unmapped", "mapped", "key"]},
        "offset": {"type": "integer"}, "limit": {"type": "integer"}}}, get_mapping_summary)

    def get_field(field_id: str):
        f = _field(field_id)
        return {"field": f.model_dump(mode="json", by_alias=True, exclude_none=True),
                "rendered": render.field_summary(f)}

    reg.register("get_field", "Full definition of one mapped field, including provenance.",
                 {"properties": {"field_id": {"type": "string"}}, "required": ["field_id"]}, get_field)

    def get_reference_logic(field_id: str):
        """The rule's origin: Databricks SQL per plant block, the business workbook text,
        any conflict between them, plus the current rule in words and its measured agreement."""
        f = _field(field_id)
        lt, text = render.logic(doc, f)
        ref = f.reference or {}
        agreement = None
        if f.s4_column and len(al.pairs):
            res = field_agreement(doc, al, field_id, sample=5)
            agreement = {k: res.get(k) for k in ("agreement", "support", "mismatch_samples", "error")}
        return {"field": field_id, "source_ref": f.source_ref, "current_logic_type": lt, "current_logic": text,
                "databricks_sql": ref.get("databricks"), "workbook": ref.get("workbook"),
                "conflict": ref.get("conflict"), "untranslated": ref.get("untranslated"),
                "provenance": f.provenance.model_dump(exclude_none=True), "agreement_with_s4": agreement}

    reg.register("get_reference_logic", "Where a rule came from (Databricks SQL per plant block, business workbook "
                 "text, conflicts between them) and how well it agrees with the S/4 data now.",
                 {"properties": {"field_id": {"type": "string"}}, "required": ["field_id"]}, get_reference_logic)

    # -- change set -------------------------------------------------------------------
    def get_changeset(section: str = "summary", change_id: str | None = None, offset: int = 0, limit: int = 30):
        c = st.changeset
        if change_id:
            ch = next((x for x in c["changes"] if x["id"] == change_id), None)
            if ch is None:
                raise ToolInputError(f"unknown change '{change_id}'")
            return ch
        if section == "summary":
            return {k: c[k] for k in ("changeset_id", "object", "baseline", "datasets", "summary")}
        if section == "changes":
            return _page(c["changes"], offset, limit)
        if section == "alignment":
            return c["alignment"]
        if section == "health":
            h = dict(c["mapping_health"])
            h["unmapped_ecc_columns"] = _page(h["unmapped_ecc_columns"], offset, limit)
            return h
        raise ToolInputError("section must be summary | changes | alignment | health")

    reg.register("get_changeset", "The diff and mapping health for this run. Sections: summary, changes, "
                 "alignment, health; or one change by id.", {"properties": {
                     "section": {"type": "string", "enum": ["summary", "changes", "alignment", "health"]},
                     "change_id": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}},
                 get_changeset)

    # -- candidates ------------------------------------------------------------------
    def list_candidates(op: str | None = None, max_confidence: float | None = None, offset: int = 0,
                        limit: int = 60):
        items = [o for o in st.ops if (not op or o.op == op)
                 and (max_confidence is None or o.confidence <= max_confidence)]
        return _page([_op_brief(o) for o in items], offset, limit)

    reg.register("list_candidates", "The draft operations in this proposal (deterministic candidates plus "
                 "any you added). Filter by op type or max confidence.", {"properties": {
                     "op": {"type": "string"}, "max_confidence": {"type": "number"},
                     "offset": {"type": "integer"}, "limit": {"type": "integer"}}}, list_candidates)

    def get_candidate(candidate_id: str):
        o = st.op(candidate_id)
        if o is None:
            raise ToolInputError(f"unknown candidate '{candidate_id}'")
        return mops.dump_op(o)

    reg.register("get_candidate", "Full detail (evidence, preview) of one draft operation.",
                 {"properties": {"candidate_id": {"type": "string"}}, "required": ["candidate_id"]}, get_candidate)

    def list_unresolved(kind: str | None = None, offset: int = 0, limit: int = 40):
        items = [u for u in st.unresolved if not kind or u["kind"] == kind]
        return _page(items, offset, limit)

    reg.register("list_unresolved", "Items the deterministic proposer could not decide (fields without a "
                 "confident ECC column, impure value mappings, crosswalk gaps).", {"properties": {
                     "kind": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}},
                 list_unresolved)

    # -- data ----------------------------------------------------------------------------
    def get_column_profile(side: str, column: str):
        _col(side, column)
        key = (side, column)
        if key not in st.profiles:
            from app.profiling.profiler import profile_series
            frame = st.ecc if side == "ecc" else st.s4
            st.profiles[key] = profile_series(frame[column])
        return st.profiles[key]

    reg.register("get_column_profile", "Profile of one column: blank rate, distinct count, lengths, type, "
                 "patterns and value counts.", {"properties": {"side": {"type": "string", "enum": ["ecc", "s4"]},
                                                                "column": {"type": "string"}},
                                                 "required": ["side", "column"]}, get_column_profile)

    def list_unmapped_columns(side: str, offset: int = 0, limit: int = 100):
        h = st.changeset["mapping_health"]
        cols = h["unmapped_ecc_columns"] if side == "ecc" else h["unmapped_s4_columns"]
        taken = {getattr(o, "column", None) for o in st.ops if o.op == "set_column_alias" and o.side == side}
        return _page([c for c in cols if c not in taken], offset, limit)

    reg.register("list_unmapped_columns", "Columns of one side not used by any field, ignore entry or draft alias.",
                 {"properties": {"side": {"type": "string", "enum": ["ecc", "s4"]}, "offset": {"type": "integer"},
                                 "limit": {"type": "integer"}}, "required": ["side"]}, list_unmapped_columns)

    def _take_rows(n: int) -> int:
        n = max(1, min(int(n), settings.LLM_MAX_SAMPLE_ROWS))
        left = settings.LLM_MAX_SAMPLE_ROWS_PER_RUN - st.sample_rows_used
        if left <= 0:
            raise ToolInputError("sample-row budget for this run is used up; rely on profiles and metrics")
        n = min(n, left)
        st.sample_rows_used += n
        return n

    def get_sample_rows(side: str, columns: list[str], n: int = 10, where_column: str | None = None,
                        where_value: str | None = None):
        cols = columns[:20]
        frame = st.ecc if side == "ecc" else st.s4
        for c in cols:
            _col(side, c)
        if where_column:
            _col(side, where_column)
            frame = frame[engine.as_text(frame[where_column]).str.strip() == (where_value or "")]
        n = _take_rows(n)
        rows = frame[cols].head(n).to_dict(orient="records")
        return {"rows": st.masker.rows(rows) if st.masker else rows, "matching_rows": int(len(frame))}

    reg.register("get_sample_rows", "Up to 50 raw rows of chosen columns from one side (optionally where a "
                 "column equals a value). Counts against a per-run row budget.", {"properties": {
                     "side": {"type": "string", "enum": ["ecc", "s4"]},
                     "columns": {"type": "array", "items": {"type": "string"}},
                     "n": {"type": "integer"}, "where_column": {"type": "string"}, "where_value": {"type": "string"}},
                     "required": ["side", "columns"]}, get_sample_rows)

    def get_paired_rows(ecc_columns: list[str], s4_columns: list[str], n: int = 10,
                        where_ecc_column: str | None = None, where_ecc_value: str | None = None,
                        only_disagreeing: bool = False):
        for c in ecc_columns:
            _col("ecc", c)
        for c in s4_columns:
            _col("s4", c)
        pairs = al.pairs
        e = al.ecc.iloc[pairs["ecc_pos"].to_numpy(dtype=int)].reset_index(drop=True)
        s = al.s4.iloc[pairs["s4_pos"].to_numpy(dtype=int)].reset_index(drop=True)
        mask = pd.Series(True, index=e.index)
        if where_ecc_column:
            _col("ecc", where_ecc_column)
            mask &= engine.as_text(e[where_ecc_column]).str.strip() == (where_ecc_value or "")
        if only_disagreeing and len(ecc_columns) == 1 and len(s4_columns) == 1:
            mask &= column_match.light_normalize(e[ecc_columns[0]]) != column_match.light_normalize(s[s4_columns[0]])
        idx = mask[mask].index
        n = _take_rows(n)
        rows = [{**{f"ECC:{c}": e.at[i, c] for c in ecc_columns[:10]},
                 **{f"S4:{c}": s.at[i, c] for c in s4_columns[:10]}} for i in idx[:n]]
        return {"rows": st.masker.rows(rows) if st.masker else rows, "matching_pairs": int(len(idx)),
                "alignment": al.stats}

    reg.register("get_paired_rows", "Aligned ECC/S4 row pairs (same business key) side by side, optionally "
                 "filtered on an ECC value or to rows where one ECC and one S4 column disagree.", {"properties": {
                     "ecc_columns": {"type": "array", "items": {"type": "string"}},
                     "s4_columns": {"type": "array", "items": {"type": "string"}},
                     "n": {"type": "integer"}, "where_ecc_column": {"type": "string"},
                     "where_ecc_value": {"type": "string"}, "only_disagreeing": {"type": "boolean"}},
                     "required": ["ecc_columns", "s4_columns"]}, get_paired_rows)

    # -- inference -------------------------------------------------------------------
    def find_column_matches(field_id: str, top_k: int = 5):
        _field(field_id)
        return column_match.match_fields(al, al.doc, field_ids=[field_id], top_k=min(top_k, 10))[field_id]

    reg.register("find_column_matches", "Ranks ECC columns for a field by value agreement on aligned rows and "
                 "label similarity.", {"properties": {"field_id": {"type": "string"}, "top_k": {"type": "integer"}},
                                       "required": ["field_id"]}, find_column_matches)

    def test_key_normalization(field_id: str, ecc_column: str):
        f = _field(field_id)
        _col("ecc", ecc_column)
        if not f.s4_column:
            raise ToolInputError("field has no S/4 column")
        return inf_keys.test_key_normalization(st.ecc[ecc_column], st.s4[f.s4_column], doc)

    reg.register("test_key_normalization", "Distinct-value overlap between an ECC column and a field's S/4 "
                 "column under each normalisation.", {"properties": {"field_id": {"type": "string"},
                                                                     "ecc_column": {"type": "string"}},
                                                      "required": ["field_id", "ecc_column"]}, test_key_normalization)

    def infer_value_map(ecc_column: str, s4_column: str):
        _col("ecc", ecc_column)
        _col("s4", s4_column)
        return value_maps.infer_value_map(al, ecc_column, s4_column)

    reg.register("infer_value_map", "On aligned rows: which S/4 value each ECC value becomes (purity, support).",
                 {"properties": {"ecc_column": {"type": "string"}, "s4_column": {"type": "string"}},
                  "required": ["ecc_column", "s4_column"]}, infer_value_map)

    def find_split(ecc_column: str, s4_column: str, source_value: str | None = None):
        _col("ecc", ecc_column)
        _col("s4", s4_column)
        return value_maps.find_split(al, ecc_column, s4_column, source_value)

    reg.register("find_split", "For ECC values that become more than one S/4 value: which column (including a "
                 "fan-out target such as __target__plant) decides it, with purity.", {"properties": {
                     "ecc_column": {"type": "string"}, "s4_column": {"type": "string"},
                     "source_value": {"type": "string"}}, "required": ["ecc_column", "s4_column"]}, find_split)

    def infer_crosswalk(field_id: str):
        _field(field_id)
        if field_id not in doc.key.fields:
            raise ToolInputError("crosswalk inference is for key fields; use infer_value_map for others")
        if not al.doc.field(field_id).ecc_column:
            raise ToolInputError("the key field has no ECC column yet")
        return value_maps.infer_crosswalk(al.doc, field_id, al.ecc, al.s4)

    reg.register("infer_crosswalk", "Crosswalk between ECC and S/4 codes of a key field (plant, valuation area) "
                 "from lineage or material coverage, with fan-out.", {"properties": {"field_id": {"type": "string"}},
                                                                      "required": ["field_id"]}, infer_crosswalk)

    # -- dry runs --------------------------------------------------------------------
    def _draft_doc(extra: list | None = None) -> MappingDoc:
        work = doc.model_copy(deep=True)
        for o in st.ops + (extra or []):
            try:
                mops.apply_op(work, o)
            except mops.OpError:
                continue
        return work

    def evaluate_field(field_id: str, ops: list[dict] | None = None):
        """Agreement of a field under the current draft, optionally with extra ops."""
        _field(field_id)
        extra = [mops.parse_op({**o, "reason": o.get("reason", "dry run")}) for o in (ops or [])]
        draft = _draft_doc()
        cand = _draft_doc(extra) if extra else draft
        if field_id in doc.key.fields or any(getattr(o, "field_id", None) in doc.key.fields for o in extra):
            return evaluate_field_change(draft, cand, st.ecc, st.s4, field_id)
        cur_al = inf_keys.Alignment(draft, al.ecc, al.s4, al.pairs, al.method)
        return {"field_id": field_id, "draft": field_agreement(draft, cur_al, field_id),
                "with_extra_ops": field_agreement(cand, cur_al, field_id) if extra else None}

    reg.register("evaluate_field", "Dry run: how well a field's expected S/4 values agree with the S/4 data "
                 "under the draft proposal, optionally with extra ops you are considering.", {"properties": {
                     "field_id": {"type": "string"}, "ops": {"type": "array", "items": {"type": "object"}}},
                     "required": ["field_id"]}, evaluate_field)

    def evaluate_filter_tool(filter: dict):
        flt = FilterSpec.model_validate(filter)
        return evaluate_filter(_draft_doc(), flt, al.ecc, al.s4)

    reg.register("evaluate_filter", "Dry run of an ECC or S4 filter under the draft: expected rows before/after "
                 "vs S/4 rows.", {"properties": {"filter": {"type": "object"}}, "required": ["filter"]},
                 evaluate_filter_tool)

    # -- editing the draft proposal ----------------------------------------------------------
    def validate_ops(ops: list[dict]):
        try:
            parsed = mops.parse_ops(ops)
        except Exception as exc:  # pydantic ValidationError
            return {"ok": False, "parse_error": str(exc)[:3000]}
        results = mops.validate_ops(_draft_doc(), parsed, st.headers())
        return {"ok": all(r["ok"] for r in results), "results": results}

    reg.register("validate_ops", "Check operations (schema, columns, cross references, dry-run apply) without "
                 "adding them.", {"properties": {"ops": {"type": "array", "items": {"type": "object"}}},
                                  "required": ["ops"]}, validate_ops)

    _META = {"op_id", "gate", "confidence", "reason", "evidence", "depends_on", "source", "status", "decision",
             "preview", "result"}

    def _payload(o) -> dict:
        return {k: v for k, v in mops.dump_op(o).items() if k not in _META}

    def _duplicate_of(o) -> str | None:
        new = _payload(o)
        for existing in st.ops:
            old = _payload(existing)
            if old == new:
                return existing.op_id
            if o.op == existing.op == "add_value_map_entries" and o.map_id == existing.map_id:
                have = {(e.from_, e.to) for e in existing.entries}
                if {(e.from_, e.to) for e in o.entries} <= have:
                    return existing.op_id
        return None

    def add_ops(ops: list[dict]):
        try:
            parsed = mops.parse_ops(ops)
        except Exception as exc:
            return {"added": [], "parse_error": str(exc)[:3000]}
        duplicates = []
        fresh = []
        for o in parsed:
            dup = _duplicate_of(o)
            if dup:
                duplicates.append({"op": o.op, "already_in_draft_as": dup,
                                   "hint": "use adjust_candidates to change its confidence or reason"})
            else:
                fresh.append(o)
        parsed = fresh
        for o in parsed:
            o.source = "llm"
            st._added += 1
            o.op_id = o.op_id or f"a{st._added:03d}"
            if st.op(o.op_id):
                o.op_id = f"a{st._added:03d}"
        results = mops.validate_ops(doc, st.ops + parsed, st.headers())[len(st.ops):]
        added, rejected = [], []
        for o, r in zip(parsed, results):
            if r["ok"]:
                st.ops.append(o)
                added.append({"id": o.op_id, "op": o.op, "warnings": r["warnings"]})
            else:
                rejected.append({"op": o.op, "errors": r["errors"]})
        return {"added": added, "rejected": rejected, "duplicates": duplicates}

    reg.register("add_ops", "Add operations to the draft proposal. Each is validated; invalid ones are "
                 "returned with errors and not added.", {"properties": {"ops": {"type": "array",
                                                                                "items": {"type": "object"}}},
                                                          "required": ["ops"]}, add_ops)

    def drop_candidates(ids: list[str], reason: str):
        drop = set(ids)
        changed = True
        while changed:  # dependents go too
            changed = False
            for o in st.ops:
                if o.op_id not in drop and set(o.depends_on) & drop:
                    drop.add(o.op_id)
                    changed = True
        kept = []
        for o in st.ops:
            if o.op_id in drop:
                st.dropped.append({"op": mops.dump_op(o), "by": "llm", "reason": reason})
            else:
                kept.append(o)
        unknown = set(ids) - {o.op_id for o in st.ops}
        st.ops = kept
        return {"dropped": sorted(drop - unknown), "unknown": sorted(unknown)}

    reg.register("drop_candidates", "Remove draft operations you judge wrong (their dependents are removed too).",
                 {"properties": {"ids": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}},
                  "required": ["ids", "reason"]}, drop_candidates)

    def adjust_candidates(updates: list[dict]):
        done, unknown = [], []
        for u in updates:
            o = st.op(u.get("id", ""))
            if o is None:
                unknown.append(u.get("id"))
                continue
            if "confidence" in u:
                o.confidence = max(0.0, min(1.0, float(u["confidence"])))
            if u.get("note"):
                o.reason = f"{o.reason} | Agent: {u['note']}"[:1500]
            done.append(o.op_id)
        return {"updated": done, "unknown": unknown}

    reg.register("adjust_candidates", "Change the confidence of draft operations and/or append a note to their "
                 "reason.", {"properties": {"updates": {"type": "array", "items": {"type": "object", "properties": {
                     "id": {"type": "string"}, "confidence": {"type": "number"}, "note": {"type": "string"}},
                     "required": ["id"]}}}, "required": ["updates"]}, adjust_candidates)

    def submit_proposal(summary: str):
        st.summary = summary[:4000]
        st.finished = True
        return {"submitted": True, "operations": len(st.ops)}

    reg.register("submit_proposal", "Finish: submit the draft proposal for human review, with a short summary "
                 "of what changed, what you verified and what needs a human decision.",
                 {"properties": {"summary": {"type": "string"}}, "required": ["summary"]}, submit_proposal)
    return reg
