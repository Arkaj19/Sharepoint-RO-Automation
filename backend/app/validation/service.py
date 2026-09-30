"""
Validation: proves an ECC extract was converted into its S/4 target
correctly, according to the YAML mapping for that object.

Every check is derived from the mapping (mappings/<OBJECT>.yaml) and run
through the shared transform engine, so the rule book, Agent 1's evidence
and these checks all interpret the rules the same way:

  1. Field coverage         - every mapped S/4 column exists; ECC columns resolve
  2. Mandatory completeness - mandatory fields have no blanks in S/4
  3. Type & length          - S/4 values fit the mapped data type / length
  4. Record count           - ECC rows after fan-out and filters vs S/4 rows
  5. Key integrity          - business keys line up, no orphans / duplicates
  6. Value transformation   - per field, expected S/4 value (ECC value after
                              transform) equals the actual S/4 value

Each rule is isolated: if one raises, it is reported as a warning with the
error instead of failing the whole request.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import numpy as np
import pandas as pd

from app.mapping import repository
from app.mapping.model import MappingDoc
from app.transforms import engine

MAX_SAMPLE_ISSUES = 20
_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%m/%d/%Y", "%Y%m%d")


class RuleStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"


@dataclass
class RuleResult:
    rule_id: str
    name: str
    status: RuleStatus
    summary: str
    details: list[dict] = field(default_factory=list)


@dataclass
class ValidationReport:
    sheet: str
    key_fields: list[str]
    ecc_row_count: int
    s4_row_count: int
    rules: list[RuleResult]
    overall_status: RuleStatus
    mapping_version: int | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class _Ctx:
    doc: MappingDoc
    ecc: pd.DataFrame           # prepared (fan-out) and filtered
    s4: pd.DataFrame            # filtered
    ecc_raw_rows: int
    s4_raw_rows: int
    filter_report: list[dict]
    ecc_keys: pd.Series | None = None
    s4_keys: pd.Series | None = None


def _s4_fields(doc: MappingDoc, s4: pd.DataFrame):
    return [f for f in doc.fields if f.s4_column and f.s4_column in s4.columns]


# -- rules ---------------------------------------------------------------------------------

def rule_field_coverage(c: _Ctx) -> RuleResult:
    missing_s4 = [f.id for f in c.doc.fields if not f.s4_column or f.s4_column not in c.s4.columns]
    broken_ecc = [f.id for f in c.doc.fields
                  if (col := c.doc.own_ecc_column_of(f)) and col not in c.ecc.columns]
    fanout = set(c.doc.fanout_fields()) | set(c.doc.routed_fields())
    unmapped = [f.id for f in c.doc.fields if not c.doc.has_source(f) and f.id not in fanout
                and not c.doc.is_derived_only(f)]
    details = [{"field": f, "issue": "S/4 column missing"} for f in missing_s4] + \
              [{"field": f, "issue": "ECC column in the mapping not found in the ECC file"} for f in broken_ecc]
    total = len(c.doc.fields)
    if missing_s4 or broken_ecc:
        return RuleResult("field_coverage", "Field Coverage", RuleStatus.FAIL,
                          f"{len(missing_s4)} S/4 column(s) missing, {len(broken_ecc)} ECC column(s) not found "
                          f"(of {total} mapped fields).", details[:MAX_SAMPLE_ISSUES])
    if unmapped:
        return RuleResult("field_coverage", "Field Coverage", RuleStatus.WARNING,
                          f"All {total} S/4 columns present; {len(unmapped)} field(s) have no ECC column in the "
                          f"mapping yet, so their values can't be checked (see Agent 1 proposals).",
                          [{"field": f, "issue": "no ECC column mapped"} for f in unmapped[:MAX_SAMPLE_ISSUES]])
    return RuleResult("field_coverage", "Field Coverage", RuleStatus.PASS,
                      f"All {total} mapped fields resolve in both files.")


def rule_mandatory_completeness(c: _Ctx) -> RuleResult:
    fields = [f for f in c.doc.fields if f.mandatory]
    issues = []
    for f in fields:
        if not f.s4_column or f.s4_column not in c.s4.columns:
            issues.append({"field": f.id, "issue": "column missing (see Field Coverage)"})
            continue
        n = int(engine.blank_mask(c.s4[f.s4_column]).sum())
        if n:
            issues.append({"field": f.id, "issue": f"{n} blank value(s)"})
    if issues:
        return RuleResult("mandatory_completeness", "Mandatory Field Completeness", RuleStatus.FAIL,
                          f"{len(issues)} mandatory field(s) have blank or missing values.", issues[:MAX_SAMPLE_ISSUES])
    return RuleResult("mandatory_completeness", "Mandatory Field Completeness", RuleStatus.PASS,
                      f"All {len(fields)} mandatory fields are fully populated.")


def _type_violations(values: pd.Series, data_type: str | None) -> int:
    vals = engine.as_text(values).str.strip()
    vals = vals[vals != ""]
    if vals.empty or data_type not in ("Number", "Date"):
        return 0
    uniques = vals.unique()
    bad = set()
    for u in uniques:
        if data_type == "Number":
            try:
                float(u.replace(",", ""))
            except ValueError:
                bad.add(u)
        else:
            for fmt in _DATE_FORMATS:
                try:
                    datetime.strptime(u, fmt)
                    break
                except ValueError:
                    continue
            else:
                bad.add(u)
    return int(vals.isin(bad).sum())


def rule_type_length_conformance(c: _Ctx) -> RuleResult:
    fields = _s4_fields(c.doc, c.s4)
    if not fields:
        return RuleResult("type_length_conformance", "Data Type & Length Conformance", RuleStatus.WARNING,
                          "No mapped S/4 column found - nothing was checked.")
    issues = []
    for f in fields:
        series = engine.as_text(c.s4[f.s4_column]).str.strip()
        if f.data_type == "Text" and f.length:
            n = int((series.str.len() > int(f.length)).sum())
            if n:
                issues.append({"field": f.id, "issue": f"{n} value(s) exceed max length {int(f.length)}"})
        n = _type_violations(series, f.data_type)
        if n:
            issues.append({"field": f.id, "issue": f"{n} value(s) don't match expected type '{f.data_type}'"})
    if issues:
        return RuleResult("type_length_conformance", "Data Type & Length Conformance", RuleStatus.FAIL,
                          f"{len(issues)} field(s) have type or length violations.", issues[:MAX_SAMPLE_ISSUES])
    return RuleResult("type_length_conformance", "Data Type & Length Conformance", RuleStatus.PASS,
                      f"All values of {len(fields)} checked field(s) conform to the mapped type and length.")


def rule_record_count(c: _Ctx) -> RuleResult:
    exp, act = len(c.ecc), len(c.s4)
    detail = {"ecc_rows_in_file": c.ecc_raw_rows, "ecc_rows_expected": exp, "s4_rows_in_file": c.s4_raw_rows,
              "s4_rows_after_filters": act, "delta": act - exp, "filters": c.filter_report}
    if exp == act:
        return RuleResult("record_count", "Record Count Reconciliation", RuleStatus.PASS,
                          f"Row counts match: {exp} expected from ECC, {act} in S/4.", [detail])
    return RuleResult("record_count", "Record Count Reconciliation", RuleStatus.FAIL,
                      f"Row count mismatch: {exp} expected from ECC (after fan-out and filters) vs {act} in S/4 "
                      f"({'+' if act - exp > 0 else ''}{act - exp}).", [detail])


def rule_key_integrity(c: _Ctx) -> RuleResult:
    key = c.doc.key.fields
    try:
        c.ecc_keys = engine.ecc_key_series(c.doc, c.ecc)
        c.s4_keys = engine.s4_key_series(c.doc, c.s4)
    except (KeyError, ValueError) as exc:
        return RuleResult("key_integrity", "Key Integrity", RuleStatus.WARNING,
                          f"Key could not be built ({exc}) - skipped.")
    e_set, s_set = set(c.ecc_keys), set(c.s4_keys)
    missing = sorted(e_set - s_set)
    orphans = sorted(s_set - e_set)
    s_dup = c.s4_keys[c.s4_keys.duplicated(keep=False)].unique()
    e_dup = c.ecc_keys[c.ecc_keys.duplicated(keep=False)].unique()

    def sample(keys):
        return [dict(zip(key, engine.split_key(k))) for k in list(keys)[:5]]

    issues = []
    if missing:
        issues.append({"issue": "keys expected from ECC but missing in S/4", "count": len(missing),
                       "sample": sample(missing)})
    if orphans:
        issues.append({"issue": "keys in S/4 with no ECC source row", "count": len(orphans), "sample": sample(orphans)})
    if len(s_dup):
        issues.append({"issue": "duplicate keys in S/4", "count": int(len(s_dup)), "sample": sample(s_dup)})
    if len(e_dup):
        issues.append({"issue": "duplicate keys expected from ECC", "count": int(len(e_dup)), "sample": sample(e_dup)})
    if issues:
        return RuleResult("key_integrity", "Key Integrity", RuleStatus.FAIL,
                          f"Key mismatch on {' + '.join(key)}: {len(issues)} issue type(s); "
                          f"{len(e_set & s_set)} keys match.", issues)
    return RuleResult("key_integrity", "Key Integrity", RuleStatus.PASS,
                      f"Every {' + '.join(key)} key matches 1:1 between ECC and S/4.")


def rule_value_transformation(c: _Ctx) -> RuleResult:
    if c.ecc_keys is None or c.s4_keys is None:
        return RuleResult("value_transformation", "Field-Level Value Transformation Accuracy", RuleStatus.WARNING,
                          "Skipped - keys could not be built (see Key Integrity).")
    e_ok = ~c.ecc_keys.duplicated(keep=False)
    s_ok = ~c.s4_keys.duplicated(keep=False)
    e_pos = pd.Series(np.arange(len(c.ecc_keys)), index=c.ecc_keys.to_numpy())[e_ok.to_numpy()]
    s_pos = pd.Series(np.arange(len(c.s4_keys)), index=c.s4_keys.to_numpy())[s_ok.to_numpy()]
    common = e_pos.index.intersection(s_pos.index)
    ei, si = e_pos.loc[common].to_numpy(), s_pos.loc[common].to_numpy()

    key_ids = set(c.doc.key.fields)
    mismatches, unmapped_values, samples = {}, {}, []
    checked, skipped = 0, []
    for f in c.doc.fields:
        if f.id in key_ids or not f.s4_column or f.s4_column not in c.s4.columns:
            continue
        try:
            exp = engine.expected_s4(c.doc, f, c.ecc)
        except (KeyError, ValueError):
            skipped.append(f.id)
            continue
        checked += 1
        e = engine.comparable(exp.values, f, c.doc).to_numpy(dtype=str)[ei]
        s = engine.comparable(c.s4[f.s4_column], f, c.doc).to_numpy(dtype=str)[si]
        bad = e != s
        n = int(bad.sum())
        if n:
            mismatches[f.id] = n
            for j in np.where(bad)[0][: max(0, MAX_SAMPLE_ISSUES - len(samples))]:
                samples.append({"key": dict(zip(c.doc.key.fields, engine.split_key(common[j]))), "field": f.id,
                                "expected": str(e[j]), "s4_value": str(s[j])})
        um = int(exp.unmapped.to_numpy()[ei].sum())
        if um:
            unmapped_values[f.id] = um
    details = [{"field": k, "mismatch_count": v} for k, v in sorted(mismatches.items(), key=lambda t: -t[1])]
    if unmapped_values:
        details.append({"values_without_value_map_entry": unmapped_values})
    if skipped:
        details.append({"not_checked_no_ecc_column": len(skipped)})
    if samples:
        details.append({"sample_mismatches": samples})
    if not checked:
        return RuleResult("value_transformation", "Field-Level Value Transformation Accuracy", RuleStatus.WARNING,
                          "No field has an ECC column in the mapping yet - nothing was compared.", details)
    if mismatches:
        return RuleResult("value_transformation", "Field-Level Value Transformation Accuracy", RuleStatus.FAIL,
                          f"{len(mismatches)} of {checked} checked field(s) have mismatched values across "
                          f"{len(common)} matched record(s).", details)
    return RuleResult("value_transformation", "Field-Level Value Transformation Accuracy", RuleStatus.PASS,
                      f"All {checked} checked field(s) match across {len(common)} matched record(s)"
                      + (f"; {len(skipped)} field(s) not checked (no ECC column)." if skipped else "."), details)


RULES = [rule_field_coverage, rule_mandatory_completeness, rule_type_length_conformance, rule_record_count,
         rule_key_integrity, rule_value_transformation]


def validate(sheet: str, ecc_df: pd.DataFrame, s4_df: pd.DataFrame, doc: MappingDoc | None = None) -> ValidationReport:
    doc = doc or repository.load(sheet)
    notes = []
    try:
        ecc = engine.prepare_ecc(doc, ecc_df)
    except (KeyError, ValueError) as exc:
        notes.append(f"fan-out could not be applied: {exc}")
        ecc = ecc_df.reset_index(drop=True)
    ecc_f, rep_e = engine.apply_filters(ecc, doc, "ecc")
    s4_f, rep_s = engine.apply_filters(s4_df.reset_index(drop=True), doc, "s4")
    ctx = _Ctx(doc, ecc_f.reset_index(drop=True), s4_f.reset_index(drop=True), len(ecc_df), len(s4_df), rep_e + rep_s)

    results = []
    for rule in RULES:
        try:
            results.append(rule(ctx))
        except Exception as exc:  # noqa: BLE001 - isolate each rule
            name = rule.__name__.removeprefix("rule_")
            results.append(RuleResult(name, name.replace("_", " ").title(), RuleStatus.WARNING,
                                      f"Rule could not run: {type(exc).__name__}: {exc}"))
    if any(r.status == RuleStatus.FAIL for r in results):
        overall = RuleStatus.FAIL
    elif any(r.status == RuleStatus.WARNING for r in results):
        overall = RuleStatus.WARNING
    else:
        overall = RuleStatus.PASS
    return ValidationReport(sheet=sheet, key_fields=[doc.field(k).s4_label for k in doc.key.fields],
                            ecc_row_count=len(ecc_df), s4_row_count=len(s4_df), rules=results,
                            overall_status=overall, mapping_version=doc.mapping_version, notes=notes)
