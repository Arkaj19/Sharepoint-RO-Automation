"""
Import the Databricks logic (and the business rule workbook) as a proposal.

  import_object("MARC") -> proposal with typed operations for review:
    * set_dictionary  technical field -> extract label, per table (curated seed
                      verified against the data, the rest found by value matching)
    * set_lookup      MARA, MARM (EA), MARD, EKGRP_UPDATED, MATNR_1025, MARC (for MBEW)
    * set_transform   the plant routing, and - when the S/4 output matches better
                      with the SQL's commented-out WHEN lines restored - an
                      alternative routing op (pick one)
    * set_source_ref / set_transform   one op per S/4 field; rules that differ by
                      plant block become per-plant cases
    * add_filter      the views' WHERE conditions and the S_MARA scope
                      (approximated from MARA; MVKE completes it when delivered)

Every op is scored against the real S/4 (Databricks) output by generating
with the imported rules and shadow-comparing: that agreement is the op's
confidence. Where the workbook's Logic Type / values disagree with the SQL
the op is flagged as a conflict (confidence capped) for human review.
Nothing is applied until a person accepts it.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from ruamel.yaml import YAML

from app.core import paths
from app.core.config import settings
from app.core.jsonio import write_json_atomic
from app.generation.service import generate, shadow_compare
from app.importers.sql_translate import Translator, Untranslatable, load_blocks, sql_of, where_conjuncts
from app.inference import column_match, keys as inf_keys
from app.ingest.readers import data_columns
from app.mapping import ops as mops
from app.mapping import repository
from app.mapping.model import (
    CaseStep, CaseWhen, Condition, DateStep, EccSource, FieldSpec, FilterSpec, FromRefStep, LookupKey, LookupSpec,
    LookupWhere, MappingDoc, NumberStep, RouteStep, StripLeadingZerosStep,
)
from app.proposals import service as proposals
from app.store import snapshot_store as ss
from app.transforms.context import dataset_frame

REFERENCE_DIR = Path(settings.REFERENCE_LOGIC_DIR) if settings.REFERENCE_LOGIC_DIR else paths.BACKEND_DIR / "reference_logic"
SQL_DIR = REFERENCE_DIR / "databricks"
LABELS_FILE = REFERENCE_DIR / "sap_labels.yaml"
SLZ = [StripLeadingZerosStep(op="strip_leading_zeros")]
CONFLICT_CAP = 0.6

LOOKUP_TEMPLATES = {
    "MARA": ("MARA", [("MATNR", "MATNR", True)], [], ["MATNR", "MTART", "MEINS", "PRDHA", "LVORM"],
             "Material master (type, base unit, hierarchy, deletion flag)"),
    "MARM_EA": ("MARM", [("MATNR", "MATNR", True)], [("MEINH", "EA")], ["UMREN", "UMREZ"],
                "Unit of measure EA: UMREN = EA per base unit (e.g. per case)"),
    "MARD": ("MARD", [("MATNR", "MATNR", True), ("WERKS", "WERKS", False)], [], ["MATNR", "LGORT", "LVORM", "WERKS"],
             "Storage location of the material in the plant"),
    "EKGRP_UPD": ("EKGRP_UPDATED", [("MATNR", "MATNR", True), ("WERKS", "WERKS", False)], [], ["EKGRP"],
                  "Purchasing-group overrides (hand-maintained table)"),
    "MARA_NEW": ("MATNR_1025", [("MATNR", "MATNR", True)], [], ["MATNR_NEW"],
                 "Material renumbering for plant 1025"),
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# -- inputs -------------------------------------------------------------------------------------

def curated_labels() -> dict[str, dict[str, str]]:
    if not LABELS_FILE.exists():
        return {}
    raw = YAML(typ="safe").load(LABELS_FILE.read_text(encoding="utf-8")) or {}
    return {t.upper(): {k.upper(): v for k, v in (m or {}).items()} for t, m in raw.items()}


def read_workbook() -> dict[str, dict]:
    """{S/4 structure: {"filters": text, "fields": {SAP field: row}}} from the
    business rule workbook (REF/RULE_WORKBOOK), or {} if it hasn't been fetched."""
    df = dataset_frame("RULE_WORKBOOK")
    if df is None or "SAP Structure" not in df.columns:
        return {}
    out: dict[str, dict] = {}
    group = None
    for _, r in df.iterrows():
        struct = str(r.get("SAP Structure", "")).strip()
        sheet_txt = str(r.get("Sheet Name", "")).strip()
        if not struct:
            continue
        entry = out.setdefault(struct, {"filters": None, "fields": {}})
        if sheet_txt.lower().startswith("filters") or "filter" in sheet_txt.lower():
            entry["filters"] = entry["filters"] or sheet_txt
        if str(r.get("Group Name", "")).strip():
            group = str(r["Group Name"]).strip()
        fld = str(r.get("SAP Field", "")).strip().upper()
        if fld:
            lt = str(r.get("Logic Type", "")).strip()
            lt_norm = "Derived" if lt.lower().startswith(("derived", "dervied")) else lt
            entry["fields"][fld] = {
                "description": str(r.get("Field Description", "")).strip(), "group": group,
                "importance": str(r.get("Importance", "")).strip(), "logic_type": lt_norm, "logic_type_raw": lt,
                "logic": str(r.get("Logic", "")).strip(), "ecc_table": str(r.get("ECC Table", "")).strip(),
            }
    return out


# -- dictionary ---------------------------------------------------------------------------------

def build_dictionary(obj: str, fields_needed: set[str], passthrough: dict[str, str], doc: MappingDoc,
                     ecc: pd.DataFrame, s4: pd.DataFrame, wb_fields: dict) -> tuple[dict[str, str], dict]:
    """ECC label for each technical field of the object's own table.
    `passthrough` = {FIELD: S/4 column} for fields the SQL copies unchanged,
    used to verify labels by value agreement."""
    headers = set(data_columns(ecc))
    labels: dict[str, str] = {}
    evidence: dict[str, dict] = {}
    for fld, lab in curated_labels().get(obj, {}).items():
        if lab in headers:
            labels[fld] = lab
            evidence[fld] = {"method": "curated"}
    for f in doc.fields:                                   # approved aliases from earlier reviews
        tf = (f.ecc_source.field or "").upper()
        if f.ecc_column and f.ecc_column in headers and tf and tf not in labels:
            labels[tf] = f.ecc_column
            evidence[tf] = {"method": "approved alias"}

    # value matching for passthrough fields (also verifies curated labels)
    todo = {fld: col for fld, col in passthrough.items() if col in s4.columns}
    if todo:
        key_fields = [k for k in doc.key.fields]
        temp_fields = [f.model_copy(deep=True) for f in doc.key_fields()]
        for kf in temp_fields:
            kf.transform, kf.compare = [], []
            kf.ecc_column = labels.get((kf.ecc_source.field or "").upper()) or kf.ecc_column
        prim = temp_fields[0]
        prim.ecc_column = labels.get("MATNR", prim.ecc_column)
        prim.compare = list(SLZ)
        existing = {kf.id for kf in temp_fields}
        for fld, col in todo.items():
            fid = f"__pt_{fld.lower()}"
            if fid in existing:
                continue
            desc = (wb_fields.get(fld) or {}).get("description") or fld
            temp_fields.append(FieldSpec(id=fid, s4_label=desc, s4_column=col))
        temp = MappingDoc(object=obj, key=doc.key.model_copy(update={"fields": key_fields}), fields=temp_fields)
        al = inf_keys.align(temp, ecc, s4)
        ids = [f"__pt_{fld.lower()}" for fld in todo]
        matches = column_match.match_fields(al, al.doc, field_ids=ids, top_k=3)
        # verify curated / approved labels
        for fld in list(labels):
            fid = f"__pt_{fld.lower()}"
            if fid in matches:
                cand = next((c for c in matches[fid] if c["column"] == labels[fld]), None)
                evidence[fld]["agreement"] = cand["agreement"] if cand else None
                best = matches[fid][0] if matches[fid] else None
                if best and best["column"] != labels[fld] and (best["agreement"] or 0) >= 0.95 \
                        and (cand is None or (cand["agreement"] or 0) < 0.8):
                    evidence[fld]["better_match"] = best
        assigned = column_match.assign({fid: c for fid, c in matches.items()
                                        if fid[len("__pt_"):].upper() not in labels})
        taken = set(labels.values())
        for fid, c in assigned.items():
            fld = fid[len("__pt_"):].upper()
            if c["column"] in taken:
                continue
            ok = (c["agreement"] is not None and c["support"] >= column_match.MIN_SUPPORT and c["agreement"] >= 0.9) \
                or (c["name_similarity"] == 1.0 and (c["agreement"] is None or c["support"] < column_match.MIN_SUPPORT))
            if ok:
                labels[fld] = c["column"]
                taken.add(c["column"])
                evidence[fld] = {"method": "value match", "agreement": c["agreement"], "support": c["support"],
                                 "name_similarity": c["name_similarity"]}
    missing = sorted(f for f in fields_needed if f not in labels)
    return {k: v for k, v in labels.items() if k in fields_needed or k in passthrough}, \
        {"entries": evidence, "missing": missing}


# -- conflict detection ---------------------------------------------------------------------------

_QUOTED = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_PLANT_CODE = re.compile(r"^(\d{4}|US\d{2}|CA\d{2})$")


def _quoted(text: str) -> set[str]:
    return {a or b for a, b in _QUOTED.findall(text or "")}


_PLACEHOLDERS = {"-", "--", "–", "—", "n/a", "na", "none"}


def _is_noise(v: str) -> bool:
    """Values that say nothing about a rule: blanks, plant codes, numbers and
    placeholders such as '-' ('none' in the workbook's Logic text)."""
    return (v.strip() == "" or v.strip().lower() in _PLACEHOLDERS or bool(_PLANT_CODE.match(v))
            or bool(re.match(r"^-?\d+(\.\d+)?$", v)))


def logic_type(steps: list) -> str:
    if not steps:
        return "Passthrough"
    if len(steps) == 1 and steps[0].op == "constant":
        return "Hardcoded"
    if all(s.op in ("date", "strip_leading_zeros", "number") for s in steps):
        return "Passthrough"          # formatting only
    return "Derived"


def conflict(wb: dict | None, sql_type: str, sql_literals: set[str]) -> dict | None:
    if not wb or not wb.get("logic_type") or wb["logic_type"] in ("N/A", ""):
        return None
    wt = wb["logic_type"]
    logic_txt = wb.get("logic", "") or ""
    if wt != sql_type:
        if wt == "Derived" and sql_type == "Passthrough":
            return None            # formatting (to_date, strip zeros) counts as derived in the workbook
        if wt == "Hardcoded" and sql_type == "Derived" and len({v for v in sql_literals if not _is_noise(v)}) <= 1:
            return None
        return {"kind": "logic_type", "workbook": wt, "sql": sql_type,
                "detail": f"Workbook says {wt}, SQL is {sql_type}"}
    if wt == "Hardcoded":
        wl = {("" if v.strip().lower() == "blank" else v) for v in _quoted(logic_txt)}
        if not wl:
            txt = logic_txt.strip()
            wl = {"" if txt.lower() in ("blank", "") else txt}
        if sql_literals and not (wl & sql_literals):
            return {"kind": "value", "workbook": sorted(wl), "sql": sorted(sql_literals),
                    "detail": f"Hardcoded value differs: workbook {sorted(wl)}, SQL {sorted(sql_literals)}"}
        # per-plant constants: every block is "hardcoded", but some plants get another value
        extra = sorted({v for v in sql_literals if not _is_noise(v)} - wl)
        if wl and extra:
            return {"kind": "value", "workbook": sorted(wl), "sql": sorted(sql_literals),
                    "detail": f"Hardcoded value differs: workbook {sorted(wl)}, SQL also sets {extra}"}
    if wt == "Derived" and logic_txt:
        wl = {x for x in _quoted(logic_txt) if not _is_noise(x)}
        sl = {x for x in sql_literals if not _is_noise(x)}
        only_wb, only_sql = sorted(wl - sl), sorted(sl - wl)
        if wl and sl and (only_wb or only_sql) and len(only_wb) + len(only_sql) <= 20:
            return {"kind": "values", "only_in_workbook": only_wb, "only_in_sql": only_sql,
                    "detail": "Values differ - only in workbook: " + (", ".join(only_wb) or "none")
                              + "; only in SQL: " + (", ".join(only_sql) or "none")}
    return None


def _literals(steps: list) -> set[str]:
    out: set[str] = set()
    for s in steps:
        d = s.model_dump(mode="json")

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k in ("value",) and isinstance(v, str):
                        out.add(v)
                    elif k == "values" and isinstance(v, list):
                        out.update(str(i) for i in v)
                    else:
                        walk(v)
            elif isinstance(x, list):
                for i in x:
                    walk(i)
        walk(d)
    return out


# -- main --------------------------------------------------------------------------------------------

def _plant_cond(obj: str, plants: list[str]) -> Condition:
    ref = "MARC.WERKS" if obj == "MARC" else "MBEW.BWKEY"
    return Condition(ref=ref, op="in", values=plants) if len(plants) > 1 else \
        Condition(ref=ref, op="eq", value=plants[0])


def _dump(steps: list) -> str:
    import json
    return json.dumps([s.model_dump(mode="json") for s in steps], sort_keys=True)


def _lookups_for(obj: str, used_aliases: set[str], marc_fields: set[str]) -> list[LookupSpec]:
    out = []
    left_alias = obj
    for alias, (table, keyspec, where, cols, desc) in LOOKUP_TEMPLATES.items():
        if alias not in used_aliases:
            continue
        lkeys = []
        for left_f, right_f, norm in keyspec:
            left = f"{left_alias}.{left_f if not (obj == 'MBEW' and left_f == 'WERKS') else 'BWKEY'}"
            lkeys.append(LookupKey(left=left, right=right_f, normalize=list(SLZ) if norm else []))
        out.append(LookupSpec(id=alias, table=table, keys=lkeys,
                              where=[LookupWhere(field=f, op="eq", value=v) for f, v in where],
                              columns=cols, description=desc))
    if obj == "MBEW" and "MARC" in used_aliases:
        out.insert(0, LookupSpec(id="MARC", table="MARC",
                                 keys=[LookupKey(left="MBEW.MATNR", right="MATNR", normalize=list(SLZ)),
                                       LookupKey(left="MBEW.BWKEY", right="WERKS")],
                                 columns=sorted(marc_fields | {"MATNR", "WERKS"}),
                                 description="Plant data of the material in the valuation area (routing inputs)"))
    return out


def _refs_in(node, tr: Translator) -> set[str]:
    from sqlglot import exp
    out = set()
    # columns inside `x IN (SELECT ...)` belong to the sub-query's own table (the MARA scope list)
    skip = {id(c) for q in node.find_all(exp.In) if q.args.get("query") is not None
            for c in q.args["query"].find_all(exp.Column)}
    for c in node.find_all(exp.Column):
        if id(c) in skip:
            continue
        try:
            out.add(tr.ref(c))
        except Exception:  # noqa: BLE001
            pass
    return out


def _zero_padded(s4: pd.DataFrame, col: str | None) -> bool:
    if not col or col not in s4.columns:
        return False
    vals = s4[col].astype(str).str.strip()
    vals = vals[vals != ""]
    return bool(len(vals)) and vals.str.match(r"^0+\d").mean() > 0.5


_STATE_KEYS = ("fields", "dictionary", "lookups", "filters", "value_maps", "key", "ignored_columns")


def _strip_provenance(x):
    if isinstance(x, dict):
        return {k: _strip_provenance(v) for k, v in x.items() if k != "provenance"}
    if isinstance(x, list):
        return [_strip_provenance(v) for v in x]
    return x


def _state(doc: MappingDoc) -> dict:
    d = doc.model_dump(mode="json")
    return _strip_provenance({k: d.get(k) for k in _STATE_KEYS})


def reconcile(doc: MappingDoc, op_list: list) -> tuple[list, list]:
    """Compares the imported ops with the current mapping, in order.
    Returns (ops to propose, ops already in the mapping unchanged).
      * an op that changes nothing is not proposed again - unless it carries a
        workbook conflict, which needs a decision even for an unchanged rule;
      * a filter whose id already exists with another condition is replaced
        (remove + add) instead of failing on apply;
      * every proposed op gets a before/after preview for the review page.
    Routing alternatives are always proposed and each is previewed against
    the current mapping, not against the other alternative."""
    work = doc.model_copy(deep=True)
    kept, unchanged = [], []
    for op in op_list:
        conflict_ = (getattr(op, "reference", None) or {}).get("conflict")
        if op.op == "add_filter":
            ex = next((x for x in work.filters if x.id == op.filter.id), None)
            if ex is not None:
                if _strip_provenance(ex.model_dump(mode="json")) == _strip_provenance(op.filter.model_dump(mode="json")):
                    unchanged.append(op)
                    continue
                rm = mops.RemoveFilter(op="remove_filter", filter_id=ex.id, source=op.source,
                                       confidence=op.confidence,
                                       reason=f"Replaced by the re-imported condition ({op.reason})")
                rm.preview = mops.preview(work, rm)
                mops.apply_op(work, rm)
                kept.append(rm)
        before = _state(work)
        trial = work.model_copy(deep=True)
        try:
            mops.apply_op(trial, op)
        except mops.OpError:
            kept.append(op)                  # apply will report why
            continue
        same = _state(trial) == before
        if not op.alternative_group and not conflict_ and same:
            unchanged.append(op)
            continue
        op.preview = mops.preview(work, op)
        fid = getattr(op, "field_id", None)
        logic = {"transform", "compare", "source_ref"}
        same_logic = same or (fid is not None and work.field(fid) is not None and
                              trial.field(fid).model_dump(mode="json", include=logic)
                              == work.field(fid).model_dump(mode="json", include=logic))
        if same_logic:                       # proposed again only for the decision, the rule itself is unchanged
            why = ("it conflicts with the business workbook - confirm or change it" if conflict_
                   else "it is one of the routing alternatives - accept exactly one")
            op.preview["after"] = f"no change to the current rule - proposed again because {why}"
        kept.append(op)
        if not op.alternative_group:
            work = trial
    return kept, unchanged


def import_object(obj: str, by: str | None = None) -> dict:
    doc = repository.load(obj)
    blocks = [b for b in load_blocks(SQL_DIR) if b.object == obj]
    if not blocks:
        raise ValueError(f"No Databricks views for {obj} in {SQL_DIR}")
    plant_fid = next(f.id for f in doc.fields if (f.s4_column or "").upper() in ("WERKS", "BWKEY"))
    tr = Translator(obj, plant_fid)
    wb_all = read_workbook()
    wb_fields = (wb_all.get(f"S_{obj}") or {}).get("fields", {})
    ecc = ss.read_combined(obj, "ECC")
    s4 = ss.read_combined(obj, "S4")

    # ---- refs used ----
    used: set[str] = set()
    for b in blocks:
        used |= _refs_in(b.mapped(), tr)
        base = b.base()
        if base is not None:
            used |= _refs_in(base, tr)
    aliases = {r.split(".")[0] for r in used if not r.startswith("TARGET.")}
    own_fields = {r.split(".")[1] for r in used if r.split(".")[0] == obj}
    marc_fields = {r.split(".")[1] for r in used if r.split(".")[0] == "MARC"} if obj == "MBEW" else set()

    # ---- per-field translation ----
    s4_upper = {c.upper(): c for c in s4.columns}
    field_by_col = {(f.s4_column or "").upper(): f for f in doc.fields}
    translations: dict[str, dict[str, tuple]] = {}   # alias -> block -> (src, steps, sql)
    errors: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for b in blocks:
        for proj in b.mapped().expressions:
            alias = proj.alias_or_name.upper()
            if alias not in order:
                order.append(alias)
            try:
                src = tr.primary_source(proj)
                steps = tr.steps(proj, src)
                translations.setdefault(alias, {})[b.name] = (src, steps, sql_of(proj))
            except Untranslatable as exc:
                errors.setdefault(alias, {})[b.name] = str(exc)

    passthrough = {}
    for alias, per in translations.items():
        vals = list(per.values())
        if all(v[0] == f"{obj}.{alias}" and logic_type(v[1]) == "Passthrough" for v in vals):
            passthrough[alias] = s4_upper.get(alias, alias)

    dictionary, dict_ev = build_dictionary(obj, own_fields, passthrough, doc, ecc, s4, wb_fields)

    op_list: list = []
    # ---- dictionary + lookups ----
    if dictionary:
        op_list.append(mops.SetDictionary(op="set_dictionary", table=obj, entries=dictionary, source="databricks",
                                      confidence=0.9, reason=f"ECC column labels for the {obj} fields the rules use "
                                      f"({len(dictionary)} found, {len(dict_ev['missing'])} missing)",
                                      evidence=dict_ev))
    lookups = _lookups_for(obj, aliases, marc_fields)
    labels = curated_labels()
    for lk in lookups:
        if lk.table == "MARC":
            marc_doc = repository.load("MARC")
            entries = {k: v for k, v in (marc_doc.dictionary.get("MARC") or {}).items()}
            entries.update({k: v for k, v in labels.get("MARC", {}).items() if k not in entries})
        else:
            entries = labels.get(lk.table, {})
        table_df = dataset_frame(lk.table)
        present = {k: v for k, v in entries.items() if table_df is None or v in table_df.columns}
        if present:
            op_list.append(mops.SetDictionary(
                op="set_dictionary", table=lk.table, entries=present, source="databricks", confidence=0.9,
                reason=f"Column labels of {lk.table}" + (" (table not delivered yet)" if table_df is None else ""),
                evidence={"available": table_df is not None}))
        op_list.append(mops.SetLookup(
            op="set_lookup", lookup=lk, source="databricks", confidence=0.95 if table_df is not None else 0.5,
            reason=f"Databricks joins {lk.table} as {lk.id}" + ("" if table_df is not None else
                                                                 " - table not available yet, rules using it will "
                                                                 "report 'input missing'"),
            evidence={"available": table_df is not None}))

    # ---- routing ----
    def branches(variant: bool) -> list:
        out = []
        for b in blocks:
            out += tr.route_branches(b, _plant_cond(obj, b.plants), variant=variant and b.variant_tree is not None)
        return out

    route_ref = f"{obj}.WERKS" if obj == "MARC" else f"{obj}.BWKEY"
    route_main = mops.SetTransform(op="set_transform", field_id=plant_fid, transform=[RouteStep(op="route",
                                   branches=branches(False))], source_ref=route_ref, source="databricks",
                                   confidence=0.5, reason="Plant routing from the Databricks base CTEs (as shared)",
                                   reference={"databricks": {b.name: sql_of(b.base()) if b.base() is not None
                                                             else "fixed plant" for b in blocks}},
                                   alternative_group=f"routing:{plant_fid}")
    op_list.append(route_main)
    restored = {b.name: b.restored_lines for b in blocks if b.restored_lines}
    route_variant = None
    if restored:
        route_variant = mops.SetTransform(
            op="set_transform", field_id=plant_fid, transform=[RouteStep(op="route", branches=branches(True))],
            source_ref=route_ref, source="databricks", confidence=0.5,
            reason="Plant routing with the SQL's commented-out WHEN lines restored",
            reference={"restored_lines": restored}, alternative_group=f"routing:{plant_fid}")
        op_list.append(route_variant)

    # ---- fields ----
    field_ops: dict[str, object] = {}
    new_fields = []
    for alias in order:
        if alias in ("WERKS", "BWKEY"):
            continue
        per = translations.get(alias, {})
        f = field_by_col.get(alias)
        wb = wb_fields.get(alias)
        if f is None:
            if alias not in s4_upper:
                continue
            fid = re.sub(r"[^a-z0-9]+", "_", (wb or {}).get("description", alias).lower()).strip("_") or alias.lower()
            if any(x.id == fid for x in doc.fields):
                fid = f"{fid}_{alias.lower()}"
            f = FieldSpec(id=fid, s4_label=(wb or {}).get("description") or alias, s4_column=s4_upper[alias],
                          ecc_source=EccSource(table=obj, field=alias), data_type="Text", length=80)
            new_fields.append(f)
            op_list.append(mops.AddField(op="add_field", field=f, source="databricks", confidence=0.8,
                                         reason=f"S/4 column {alias} is produced by the Databricks views but was "
                                                f"not in the mapping"))
        if errors.get(alias) and not per:
            continue
        srcs = {v[0] for v in per.values()}
        common_src = next(iter(srcs)) if len(srcs) == 1 else None
        dumps = {_dump(v[1]) for v in per.values()}
        if len(dumps) == 1 and len(srcs) == 1:
            src, steps = common_src, next(iter(per.values()))[1]
        else:
            src = common_src
            cases = []
            for b in blocks:
                if b.name not in per:
                    continue
                bsrc, bsteps, _ = per[b.name]
                if src != bsrc and bsrc:
                    bsteps = [FromRefStep(op="from_ref", ref=bsrc)] + bsteps
                cases.append(CaseWhen(when=_plant_cond(obj, b.plants), steps=bsteps))
            steps = [CaseStep(op="case", cases=cases, otherwise=[])]
        compare = [NumberStep(op="number")] if f.data_type == "Number" else \
            [DateStep(op="date")] if f.data_type == "Date" else []
        if f.id in doc.key.fields and (src or "").upper().endswith(".MATNR") and _zero_padded(s4, f.s4_column):
            compare = list(SLZ)          # S/4 material numbers are zero-padded (18), ECC's are not
        texts = {bn: v[2] for bn, v in per.items()}
        reference = {"databricks": {"all plant blocks": next(iter(texts.values()))}
                     if len(set(texts.values())) == 1 else texts}
        if errors.get(alias):
            reference["untranslated"] = errors[alias]
        if wb:
            reference["workbook"] = {k: wb[k] for k in ("logic_type", "logic", "description", "group", "importance")}
        ltypes = {logic_type(v[1]) for v in per.values()}
        ltype = ltypes.pop() if len(ltypes) == 1 else "Derived"
        cf = conflict(wb, ltype, _literals(steps))
        if cf:
            reference["conflict"] = cf
        if not steps and not compare:
            op = mops.SetSourceRef(op="set_source_ref", field_id=f.id, source_ref=src, source="databricks",
                                   confidence=0.5, reason=f"Passthrough of {src}", reference=reference)
        else:
            op = mops.SetTransform(op="set_transform", field_id=f.id, transform=steps, compare=compare,
                                   source_ref=src or "", source="databricks", confidence=0.5,
                                   reason=f"{ltype} rule from the Databricks views"
                                          + (f" ({len(per)} plant blocks differ)" if len(dumps) > 1 else ""),
                                   reference=reference)
        field_ops[f.id] = op
        op_list.append(op)

    # ---- filters ----
    conj_blocks: dict[str, tuple[Condition, list[str], str]] = {}
    for b in blocks:
        for c in where_conjuncts(b.mapped()):
            text = sql_of(c)
            low = text.lower()
            if "derived_werks" in low or re.search(r"\b(werks|bwkey)\b\s*(=|in)", low):
                continue
            try:
                if " in (select" in low:
                    sub = c.args["query"].find(__import__("sqlglot").exp.Select)
                    t2 = Translator("MARA", plant_fid, default_table="MARA")
                    cond = Condition(all=[Condition(ref="MARA.MATNR", op="not_null")]
                                     + [t2.cond(x) for x in where_conjuncts(sub)])
                else:
                    cond = tr.cond(c)
            except Untranslatable:
                continue
            key = cond.model_dump_json()
            prev = conj_blocks.get(key)
            conj_blocks[key] = (cond, (prev[1] if prev else []) + [b.name], text)
    all_names = [b.name for b in blocks]
    n = 0
    for key, (cond, names, text) in conj_blocks.items():
        n += 1
        if set(names) != set(all_names):
            plants = [p for b in blocks if b.name in names for p in b.plants]
            pref = "MARC.WERKS" if obj == "MARC" else "MBEW.BWKEY"
            other_plant = Condition(ref=pref, op="not_in", values=plants) if len(plants) > 1 else \
                Condition(ref=pref, op="ne", value=plants[0])
            cond = Condition(any=[other_plant, cond])
        op_list.append(mops.AddFilter(op="add_filter", filter=FilterSpec(
            id=f"F-{obj}-db-{n:02d}", side="ecc", expr=cond, source_text=text), source="databricks",
            confidence=0.9, reason=f"WHERE condition of the Databricks views ({', '.join(names)})"))
    scope = FilterSpec(
        id=f"F-{obj}-scope", side="ecc",
        expr=Condition(all=[Condition(ref="MARA.MATNR", op="not_null"), Condition(ref="MARA.LVORM", op="is_null"),
                            Condition(ref="MARA.MTART", op="ne", value="NVAL", sql_null=True)]),
        source_text="INNER JOIN mm_S_MARA - approximated from the S_MARA rules: material in MARA, not deleted, "
                    "not NVAL. The MVKE (sales record not deleted) part needs the MVKE extract.")
    op_list.append(mops.AddFilter(op="add_filter", filter=scope, source="databricks", confidence=0.8,
                                  reason="Migration scope (materials in S_MARA), approximated without MVKE"))

    # ---- score against the real S/4 output ----
    def build(with_variant: bool) -> MappingDoc:
        work = doc.model_copy(deep=True)
        skip = route_main if with_variant else route_variant
        for op in op_list:
            if skip is not None and op is skip:
                continue
            try:
                mops.apply_op(work, op)
            except mops.OpError:
                pass
        return mops.revalidate(work)

    report: dict = {"object": obj, "created_at": _now(), "blocks": [{"name": b.name, "plants": b.plants,
                                                                    "restored_lines": b.restored_lines}
                                                                   for b in blocks],
                    "untranslated": errors, "dictionary_missing": dict_ev["missing"]}
    main_doc = build(False)
    gen = generate(main_doc, ecc)
    shadow = shadow_compare(main_doc, gen.frame, s4, gen.missing) if not gen.error else None
    report["main"] = {"error": gen.error, "missing_inputs": gen.missing_inputs,
                      "rows": shadow["rows"] if shadow else None, "summary": shadow["summary"] if shadow else None}
    per_field = {x["field"]: x for x in (shadow or {}).get("fields", [])}
    for fid, op in field_ops.items():
        st = per_field.get(fid) or {}
        if st.get("agreement") is not None:
            op.confidence = round(float(st["agreement"]), 2)
            op.evidence = {"agreement": st["agreement"], "compared": st["compared"], "mismatches": st["mismatches"],
                           "samples": st.get("samples", [])[:5]}
        elif st.get("status") in ("input_missing", "no_rule"):
            op.confidence = 0.5
            op.evidence = {"status": st["status"], "reason": st.get("reason")}
        ref = getattr(op, "reference", None) or {}
        if ref.get("conflict"):
            op.confidence = min(op.confidence, CONFLICT_CAP)
            op.reason += f" - CONFLICT with the workbook: {ref['conflict']['detail']}"
    if shadow:
        r = shadow["rows"]
        route_main.confidence = round(float(r["theirs_reproduced_share"] or 0), 2)
        route_main.evidence = {k: r[k] for k in ("theirs_reproduced_share", "ours_confirmed_share", "keys_both",
                                                  "keys_only_ours", "keys_only_theirs")}
    if route_variant is not None:
        vdoc = build(True)
        vgen = generate(vdoc, ecc)
        vshadow = shadow_compare(vdoc, vgen.frame, s4, vgen.missing) if not vgen.error else None
        report["variant"] = {"error": vgen.error, "rows": vshadow["rows"] if vshadow else None,
                             "summary": vshadow["summary"] if vshadow else None}
        if vshadow:
            vr = vshadow["rows"]
            route_variant.confidence = round(float(vr["theirs_reproduced_share"] or 0), 2)
            route_variant.evidence = {k: vr[k] for k in ("theirs_reproduced_share", "ours_confirmed_share",
                                                          "keys_both", "keys_only_ours", "keys_only_theirs")}
            if shadow and (vr["theirs_reproduced_share"] or 0) - (shadow["rows"]["theirs_reproduced_share"] or 0) > 0.05:
                msg = (f"DRIFT: the S/4 output matches the SQL with its commented-out lines restored "
                       f"({vr['theirs_reproduced_share']:.1%} of S/4 rows reproduced) better than the SQL as shared "
                       f"({shadow['rows']['theirs_reproduced_share']:.1%}). Confirm with the Databricks team which "
                       f"version is current.")
                route_variant.reason += " - " + msg
                route_main.reason += " - see the alternative: " + msg
                report["drift"] = msg

    # ---- store as a proposal: only what differs from the current mapping ----
    op_list, unchanged = reconcile(doc, op_list)
    report["unchanged_ops"] = len(unchanged)
    counts = Counter(o.op for o in op_list)
    conflicts = [fid for fid, op in field_ops.items() if (getattr(op, "reference", None) or {}).get("conflict")]
    summary_lines = [
        f"Import of the Databricks logic for {obj} ({len(blocks)} plant blocks: "
        f"{', '.join(b.name for b in blocks)}).",
        "Operations: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) + ".",
    ]
    if shadow:
        s = shadow["summary"]
        summary_lines.append(
            f"Generated with these rules and compared with the Databricks S/4 output: "
            f"{shadow['rows']['theirs_reproduced_share']:.1%} of their rows reproduced, "
            f"{s['fields_matching']} fields match exactly, {s['fields_mismatching']} differ, "
            f"{s['fields_input_missing']} need a missing input.")
    if report.get("drift"):
        summary_lines.append(report["drift"])
    if unchanged:
        summary_lines.append(f"{len(unchanged)} imported rule(s) are already in the mapping unchanged - "
                             f"not proposed again.")
    if conflicts:
        summary_lines.append(f"{len(conflicts)} field(s) conflict with the business workbook - human review: "
                             + ", ".join(conflicts[:15]) + ("..." if len(conflicts) > 15 else ""))
    if errors:
        summary_lines.append(f"{len(errors)} column(s) could not be translated: {', '.join(list(errors)[:10])}")
    if dict_ev["missing"]:
        summary_lines.append(f"Dictionary: no ECC column found for {', '.join(dict_ev['missing'][:12])}")
    if gen.missing_inputs:
        summary_lines.append(f"Input missing: {', '.join(sorted(set(gen.missing_inputs)))} - those rules are "
                             f"imported but can't produce values until the tables are delivered.")
    p = proposals.create(obj, op_list, run_id=None, changeset_id=None, mode="import",
                         summary="\n".join(summary_lines), base_mapping_version=doc.mapping_version,
                         dropped=[{"op": mops.dump_op(o), "by": "import", "reason": "already in the mapping unchanged"}
                                  for o in unchanged])
    report["proposal_id"] = p["proposal_id"]
    write_json_atomic(paths.imports_dir() / f"IMPORT-{obj}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json",
                      report)
    return report
