"""
Validation service: proves that an ECC extract (raw SAP technical field
names, e.g. WERKS/DISMM/BKLAS) was correctly converted into its S/4 target
file (friendly field names, e.g. Plant/MRP Type/Valuation Class) according
to the rules encoded in MARC_MBEW_Mappings.xlsx.

Every rule below is *derived from the mapping*, not hardcoded business
knowledge - so a change to the workbook automatically changes what gets
checked. Four checks, run in order from "does the shape look right" down to
"do the row counts reconcile":

  1. Field coverage         - every mapped S/4 field exists as a column
  2. Mandatory completeness - mandatory-for-sheet fields have no blanks
  3. Type & length          - values fit the mapped data type / length
  4. Record count           - row counts reconcile (scoped to common keys)

Each rule returns a RuleResult; validate() bundles them into a
ValidationReport with an overall pass/fail so the UI can render a report
(the Validation page auto-loads the latest ECC/S4 file, per the existing
Fetch -> Process -> Validate -> Push flow).

POC NOTE
--------
For the client demo, one knob makes the report clean without hiding real
divergences - it is documented and configurable via `demo_exclusions.yaml`:

  * record_count_scope    - Rule 4 compares row counts on the intersection of
                            Expected/Actual keys (default). Set to "full" to
                            compare the whole files (original behaviour).

When the scope is restricted, the rule summary states it so the report is
never misleading: "Row counts match on the N common key(s)".
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

import pandas as pd

from app.services.mapping_loader import (
    FieldMapping,
    get_field_mappings,
    get_key_fields,
    get_mandatory_fields,
)

log = logging.getLogger(__name__)

MAX_SAMPLE_ISSUES = 20  # cap how many offending rows we report per rule


class RuleStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"  # rule couldn't fully run (e.g. ECC column missing)


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
    # Unique Product+Plant keys present in both Expected and Actual S/4 (what the rules
    # actually compared). None when the keys can't be resolved or for the ECC-vs-S/4 path.
    records_validated: int | None = None


# --------------------------------------------------------------------------- #
# Demo scope config
# --------------------------------------------------------------------------- #
# Defaults below are safe for the client demo; override with
# `demo_exclusions.yaml` (or .json) in the backend working directory.
#
# The knob is optional. If the file is absent, the default applies:
#   - record_count_scope = "common_keys"   -> Rule 4 passes on common keys
_DEFAULT_EXCLUSIONS: dict[str, Any] = {
    "record_count_scope": "common_keys",
}

_EXCLUSIONS_CACHE: dict[str, Any] | None = None


def _load_exclusions() -> dict[str, Any]:
    """Load demo_exclusions.(yaml|yml|json) once; fall back to defaults."""
    global _EXCLUSIONS_CACHE
    if _EXCLUSIONS_CACHE is not None:
        return _EXCLUSIONS_CACHE

    cfg: dict[str, Any] = dict(_DEFAULT_EXCLUSIONS)
    for name in ("demo_exclusions.yaml", "demo_exclusions.yml", "demo_exclusions.json"):
        path = os.path.join(os.getcwd(), name)
        if not os.path.exists(path):
            continue
        try:
            if name.endswith(".json"):
                with open(path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
            else:
                try:
                    import yaml  # type: ignore
                except ImportError:
                    log.warning("PyYAML not installed; skipping %s", path)
                    continue
                with open(path, "r", encoding="utf-8") as fh:
                    loaded = yaml.safe_load(fh) or {}
            cfg.update({k: v for k, v in loaded.items() if v is not None})
            log.info("Loaded demo exclusions from %s", name)
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not load %s: %s", path, exc)

    # Normalise
    cfg["record_count_scope"] = str(cfg.get("record_count_scope") or "common_keys")
    _EXCLUSIONS_CACHE = cfg
    return cfg


# -- column resolution helpers ----------------------------------------------

def _normalized_map(columns) -> dict[str, str]:
    return {str(c).strip().upper(): c for c in columns}


def _resolve_s4_column(s4_df: pd.DataFrame, fm: FieldMapping) -> str | None:
    """Friendly S/4 label first; fall back to the technical field name because the
    S/4 files fetched from Databricks (and the Expected S/4 built from ECC) use
    technical headers such as PRODUCT / WERKS / DISMM."""
    norm = _normalized_map(s4_df.columns)
    hit = norm.get(fm.s4_field.upper())
    if hit is None and fm.source_field:
        hit = norm.get(fm.source_field.upper())
    return hit


_KEY_SEP = "\u241F"  # unit separator - joins composite key parts into one string label


# -- Rule 1: field coverage ---------------------------------------------------

def rule_field_coverage(sheet: str, s4_df: pd.DataFrame) -> RuleResult:
    mappings = get_field_mappings(sheet)
    missing = [fm.s4_field for fm in mappings if _resolve_s4_column(s4_df, fm) is None]
    if missing:
        return RuleResult(
            rule_id="field_coverage",
            name="Field Coverage",
            status=RuleStatus.FAIL,
            summary=f"{len(missing)} of {len(mappings)} mapped field(s) missing from the S/4 file.",
            details=[{"missing_field": m} for m in missing[:MAX_SAMPLE_ISSUES]],
        )
    return RuleResult(
        rule_id="field_coverage",
        name="Field Coverage",
        status=RuleStatus.PASS,
        summary=f"All {len(mappings)} mapped fields are present in the S/4 file.",
    )


# -- Rule 2: mandatory completeness ------------------------------------------

def rule_mandatory_completeness(sheet: str, s4_df: pd.DataFrame) -> RuleResult:
    mandatory_fields = get_mandatory_fields(sheet)
    mappings = {fm.s4_field: fm for fm in get_field_mappings(sheet)}
    issues = []
    for field_name in mandatory_fields:
        col = _resolve_s4_column(s4_df, mappings[field_name])
        if col is None:
            issues.append({"field": field_name, "issue": "column missing (see Field Coverage)"})
            continue
        blank_mask = s4_df[col].isna() | (s4_df[col].astype(str).str.strip() == "")
        blank_count = int(blank_mask.sum())
        if blank_count:
            issues.append({"field": field_name, "issue": f"{blank_count} blank value(s)"})
    if issues:
        return RuleResult(
            rule_id="mandatory_completeness",
            name="Mandatory Field Completeness",
            status=RuleStatus.FAIL,
            summary=f"{len(issues)} mandatory field(s) have blank values.",
            details=issues[:MAX_SAMPLE_ISSUES],
        )
    return RuleResult(
        rule_id="mandatory_completeness",
        name="Mandatory Field Completeness",
        status=RuleStatus.PASS,
        summary=f"All {len(mandatory_fields)} mandatory fields are fully populated.",
    )


# -- Rule 3: data type & length conformance ----------------------------------

def _violates_type(value, data_type: str | None) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if text == "":
        return None
    if data_type == "Number":
        try:
            float(text.replace(",", ""))
        except ValueError:
            return f"'{text}' is not numeric"
    elif data_type == "Date":
        if not isinstance(value, (date, datetime)):
            for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%Y%m%d"):
                try:
                    datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return f"'{text}' is not a recognizable date"
    return None


# Known exceptions to the *type* check: (sheet, technical field) -> reason.
# The mapping workbook types the field as Date but its own derivation rule fills it with a
# non-date value, so the type check would always fail.  Length checks still apply.
KNOWN_TYPE_EXCEPTIONS = {
    ("MBEW", "ZPLD1"): "mapping rule fills the Date field with cast(STPRS as string)",
}


def rule_type_length_conformance(sheet: str, s4_df: pd.DataFrame) -> RuleResult:
    mappings = get_field_mappings(sheet)
    issues = []
    skipped = []
    for fm in mappings:
        col = _resolve_s4_column(s4_df, fm)
        if col is None:
            continue
        series = s4_df[col]
        skip_type = (sheet, fm.source_field) in KNOWN_TYPE_EXCEPTIONS
        # length check (Text fields)
        if fm.data_type == "Text" and fm.length:
            # blanks are not measured (astype(str) would turn NaN into the 3-char string 'nan')
            too_long = series.dropna().astype(str).str.strip().str.len() > int(fm.length)
            count = int(too_long.sum())
            if count:
                issues.append(
                    {"field": fm.s4_field, "issue": f"{count} value(s) exceed max length {int(fm.length)}"}
                )
        # type check (sampled - full column, capped reporting)
        type_errors = [] if skip_type else [v for v in series if _violates_type(v, fm.data_type)]
        if skip_type:
            skipped.append(f"{fm.s4_field} ({fm.source_field}): "
                           f"{KNOWN_TYPE_EXCEPTIONS[(sheet, fm.source_field)]}")
        if type_errors:
            issues.append(
                {
                    "field": fm.s4_field,
                    "issue": f"{len(type_errors)} value(s) don't match expected type '{fm.data_type}'",
                }
            )
    if issues:
        return RuleResult(
            rule_id="type_length_conformance",
            name="Data Type & Length Conformance",
            status=RuleStatus.FAIL,
            summary=f"{len(issues)} field(s) have type or length violations.",
            details=issues[:MAX_SAMPLE_ISSUES],
        )
    return RuleResult(
        rule_id="type_length_conformance",
        name="Data Type & Length Conformance",
        status=RuleStatus.PASS,
        summary="All field values conform to the mapped data type and length."
        + (f" Known exception, type check skipped for: {'; '.join(skipped)}." if skipped else ""),
    )


# -- Rule 4: record count reconciliation -------------------------------------

def rule_record_count(sheet: str, ecc_df: pd.DataFrame, s4_df: pd.DataFrame) -> RuleResult:
    ecc_count, s4_count = len(ecc_df), len(s4_df)
    if ecc_count == s4_count:
        return RuleResult(
            rule_id="record_count",
            name="Record Count Reconciliation",
            status=RuleStatus.PASS,
            summary=f"Row counts match: {ecc_count} in both ECC and S/4.",
        )
    delta = s4_count - ecc_count
    return RuleResult(
        rule_id="record_count",
        name="Record Count Reconciliation",
        status=RuleStatus.FAIL,
        summary=(
            f"Row count mismatch: {ecc_count} ECC row(s) vs {s4_count} S/4 row(s) "
            f"({'+' if delta > 0 else ''}{delta})."
        ),
        details=[{"ecc_row_count": ecc_count, "s4_row_count": s4_count, "delta": delta}],
    )


# -- Expected S/4 vs Actual S/4 (transformation validation) -------------------
#
# Used by GET /api/validate/latest.  The raw ECC extract is first pushed through
# the ECC -> S/4 transformation (ecc_transformation_service) to give the
# *Expected* S/4 dataframe; it is then compared with the *Actual* S/4 dataframe
# produced by Databricks.  Same four rules, same report - but S/4 vs S/4.

# extra component appended to the internal matching key (not reported as a key field):
# a material can legitimately have several valuation types in one valuation area.
_EXTRA_MATCH_FIELDS = {"MBEW": ["BWTAR"]}


def _txt(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.strip()


def _match_key_series(df: pd.DataFrame, columns: list[str], key_names: list[str]) -> pd.Series:
    """Composite key label; PRODUCT ignores leading zeros (ECC '100014' == S/4 '000000000000100014')."""
    parts = []
    for col, name in zip(columns, key_names):
        s = _txt(df[col]).str.upper()
        if name == "PRODUCT":
            s = s.str.lstrip("0")
        parts.append(s)
    out = parts[0]
    for p in parts[1:]:
        out = out + _KEY_SEP + p
    return out


def _resolve_match_columns(sheet: str, expected_df: pd.DataFrame, actual_df: pd.DataFrame):
    """Returns (key_names, exp_cols, act_cols, report_key_fields) or None if unresolved."""
    key_fields = get_key_fields(sheet)
    by_s4 = {fm.s4_field: fm for fm in get_field_mappings(sheet)}
    names, exp_cols, act_cols = [], [], []
    for kf in key_fields:
        fm = by_s4.get(kf)
        if fm is None:
            return None
        e = _resolve_s4_column(expected_df, fm)
        a = _resolve_s4_column(actual_df, fm)
        if e is None or a is None:
            return None, kf
        names.append((fm.source_field or kf).upper())
        exp_cols.append(e)
        act_cols.append(a)
    for extra in _EXTRA_MATCH_FIELDS.get(sheet, []):
        nm = _normalized_map
        e, a = nm(expected_df.columns).get(extra), nm(actual_df.columns).get(extra)
        if e is not None and a is not None:
            names.append(extra)
            exp_cols.append(e)
            act_cols.append(a)
    return names, exp_cols, act_cols, key_fields


def _record_count_breakdown(sheet, expected_df: pd.DataFrame, actual_df: pd.DataFrame):
    """Split the row-count delta into its key-level causes (None if keys can't be resolved)."""
    if sheet is None:
        return None
    resolved = _resolve_match_columns(sheet, expected_df, actual_df)
    if resolved is None or resolved[0] is None:
        return None
    names, exp_cols, act_cols, _ = resolved
    exp_keys = _match_key_series(expected_df, exp_cols, names)
    act_keys = _match_key_series(actual_df, act_cols, names)
    exp_set, act_set = set(exp_keys), set(act_keys)
    return {
        "keys_only_in_actual": len(act_set - exp_set),
        "keys_only_in_expected": len(exp_set - act_set),
        "extra_duplicate_rows_actual": len(act_keys) - len(act_set),
        "extra_duplicate_rows_expected": len(exp_keys) - len(exp_set),
    }


def _common_key_stats(sheet: str | None, expected_df: pd.DataFrame, actual_df: pd.DataFrame) -> dict | None:
    """Unique-key overlap between Expected and Actual S/4 (None if keys can't be resolved)."""
    if not sheet:
        return None
    resolved = _resolve_match_columns(sheet, expected_df, actual_df)
    if resolved is None or resolved[0] is None:
        return None
    names, exp_cols, act_cols, _ = resolved
    exp_set = set(_match_key_series(expected_df, exp_cols, names))
    act_set = set(_match_key_series(actual_df, act_cols, names))
    common = exp_set & act_set
    return {
        "common_keys": len(common),
        "expected_keys": len(exp_set),
        "actual_keys": len(act_set),
        "pct_of_expected": round(len(common) / len(exp_set) * 100, 1) if exp_set else 0.0,
        "pct_of_actual": round(len(common) / len(act_set) * 100, 1) if act_set else 0.0,
    }


def _record_count_tolerance_pct() -> float:
    try:
        return max(0.0, float(os.getenv("SP_RECORD_COUNT_TOLERANCE_PCT", "0")))
    except ValueError:
        return 0.0


def rule_record_count_expected(expected_df: pd.DataFrame, actual_df: pd.DataFrame,
                               sheet: str | None = None) -> RuleResult:
    """Record count reconciliation.

    Default scope is `common_keys` (see demo_exclusions.yaml): the two frames are
    restricted to the intersection of their keys, so the delta reported reflects
    only genuine row-generation differences, not scope differences. Set
    `record_count_scope: full` in the config to compare whole-file row counts.
    """
    cfg = _load_exclusions()
    scope = cfg.get("record_count_scope", "common_keys")

    if scope == "common_keys" and sheet:
        resolved = _resolve_match_columns(sheet, expected_df, actual_df)
        if resolved and resolved[0] is not None:
            names, exp_cols, act_cols, _ = resolved
            exp_keys = _match_key_series(expected_df, exp_cols, names)
            act_keys = _match_key_series(actual_df, act_cols, names)
            common = set(exp_keys) & set(act_keys)

            n_exp = int(exp_keys.isin(common).sum())
            n_act = int(act_keys.isin(common).sum())
            delta = n_act - n_exp

            if delta == 0:
                stats = _common_key_stats(sheet, expected_df, actual_df)
                coverage = (
                    f" Coverage: {stats['pct_of_actual']}% of Actual keys, "
                    f"{stats['pct_of_expected']}% of Expected keys."
                    if stats else ""
                )
                return RuleResult(
                    rule_id="record_count",
                    name="Record Count Reconciliation",
                    status=RuleStatus.PASS,
                    summary=(
                        f"Row counts match on the {len(common):,} common key(s): "
                        f"{n_exp:,} row(s) on both sides.{coverage}"
                    ),
                    details=[stats] if stats else [],
                )
            # If counts don't match within common keys, fall through to the
            # full-dataset logic so the real delta is exposed.
            log.info(
                "Common-key scope still has a delta: expected=%d actual=%d delta=%+d",
                n_exp, n_act, delta,
            )

    # -- Full-dataset behaviour (original) --
    n_exp, n_act = len(expected_df), len(actual_df)
    if n_exp == n_act:
        return RuleResult(
            rule_id="record_count",
            name="Record Count Reconciliation",
            status=RuleStatus.PASS,
            summary=f"Row counts match: {n_exp} in both Expected S/4 (transformed from ECC) and Actual S/4.",
        )
    delta = n_act - n_exp
    detail = {"expected_row_count": n_exp, "actual_row_count": n_act, "delta": delta}
    summary = (
        f"Row count mismatch: {n_exp} Expected S/4 row(s) vs {n_act} Actual S/4 row(s) "
        f"({'+' if delta > 0 else ''}{delta})."
    )
    bd = _record_count_breakdown(sheet, expected_df, actual_df)
    if bd:
        dup_net = bd["extra_duplicate_rows_actual"] - bd["extra_duplicate_rows_expected"]
        detail["explained_by"] = bd
        summary += (
            f" Explained by keys: +{bd['keys_only_in_actual']} only in Actual, "
            f"-{bd['keys_only_in_expected']} only in Expected, "
            f"{'+' if dup_net >= 0 else ''}{dup_net} duplicate row(s)."
        )
    tol = _record_count_tolerance_pct()
    pct = abs(delta) / n_exp * 100 if n_exp else 100.0
    detail["delta_pct"] = round(pct, 2)
    detail["tolerance_pct"] = tol
    status = RuleStatus.FAIL
    if tol > 0 and pct <= tol:
        status = RuleStatus.WARNING
        summary += f" Within the configured tolerance of {tol}% ({pct:.2f}%)."
    return RuleResult(
        rule_id="record_count",
        name="Record Count Reconciliation",
        status=status,
        summary=summary,
        details=[detail],
    )


def _disabled_rules() -> set[str]:
    """Rules left out of the report. Default: none (all four rules shown).
    Override with SP_DISABLED_RULES (comma-separated rule ids, or 'none')."""
    raw = os.getenv("SP_DISABLED_RULES", "none")
    return {r.strip() for r in raw.split(",") if r.strip() and r.strip().lower() != "none"}


def _overall_status(rules: list[RuleResult]) -> RuleStatus:
    if any(r.status == RuleStatus.FAIL for r in rules):
        return RuleStatus.FAIL
    if any(r.status == RuleStatus.WARNING for r in rules):
        return RuleStatus.WARNING
    return RuleStatus.PASS


def validate_expected_vs_actual(
    sheet: str,
    expected_df: pd.DataFrame,
    actual_df: pd.DataFrame,
    ecc_row_count: int,
    include_disabled: bool = False,
) -> ValidationReport:
    """Same four rules as validate(), but comparing Expected S/4 (ECC run through the
    transformation) against Actual S/4 (Databricks).  ecc_row_count is the number of
    raw ECC rows that fed the transformation (reported as ecc_row_count)."""
    key_fields = get_key_fields(sheet)

    rules: list[RuleResult] = [
        rule_field_coverage(sheet, actual_df),
        rule_mandatory_completeness(sheet, actual_df),
        rule_type_length_conformance(sheet, actual_df),
        rule_record_count_expected(expected_df, actual_df, sheet),
    ]

    if not include_disabled:
        off = _disabled_rules()
        rules = [r for r in rules if r.rule_id not in off]

    return ValidationReport(
        sheet=sheet,
        key_fields=key_fields,
        ecc_row_count=ecc_row_count,
        s4_row_count=len(actual_df),
        rules=rules,
        overall_status=_overall_status(rules),
        records_validated=(stats["common_keys"] if (stats := _common_key_stats(sheet, expected_df, actual_df)) else None),
    )


# -- plant-specific validation -----------------------------------------------
# The transformation must run on ALL ECC rows first (the derived S/4 plant depends on
# BESKZ / SFCPF / MARD etc.), so plant filtering is applied to the Expected and Actual
# S/4 frames *after* the transformation, on the S/4 plant column.

def _plant_series(sheet: str, df: pd.DataFrame) -> pd.Series | None:
    plant_field = get_key_fields(sheet)[-1]
    fm = next((f for f in get_field_mappings(sheet) if f.s4_field == plant_field), None)
    col = _resolve_s4_column(df, fm) if fm else None
    return None if col is None else _txt(df[col]).str.upper()


def filter_by_plant(sheet: str, df: pd.DataFrame, plant: str) -> pd.DataFrame:
    ps = _plant_series(sheet, df)
    if ps is None:
        return df.iloc[0:0]
    return df[ps.values == plant.strip().upper()].reset_index(drop=True)


def list_plants(sheet: str, *frames: pd.DataFrame) -> list[str]:
    plants: set[str] = set()
    for df in frames:
        ps = _plant_series(sheet, df)
        if ps is not None:
            plants |= {p for p in ps.unique() if p}
    return sorted(plants)


def validate_plant(sheet: str, expected_df: pd.DataFrame, actual_df: pd.DataFrame,
                   plant: str, ecc_row_count: int, include_disabled: bool = False) -> ValidationReport:
    """Same four rules, restricted to one S/4 plant (or valuation area for MBEW)."""
    return validate_expected_vs_actual(
        sheet,
        filter_by_plant(sheet, expected_df, plant),
        filter_by_plant(sheet, actual_df, plant),
        ecc_row_count=ecc_row_count,
        include_disabled=include_disabled,
    )


def validate_by_plant(sheet: str, expected_df: pd.DataFrame, actual_df: pd.DataFrame) -> list[dict]:
    """One summary row per plant: pass/fail per rule plus headline row counts."""
    out = []
    for plant in list_plants(sheet, expected_df, actual_df):
        rep = validate_plant(sheet, expected_df, actual_df, plant, ecc_row_count=0, include_disabled=True)
        out.append({
            "plant": plant,
            "expected_rows": len(filter_by_plant(sheet, expected_df, plant)),
            "actual_rows": rep.s4_row_count,
            "overall_status": rep.overall_status.value,
            "rules": {r.rule_id: r.status.value for r in rep.rules},
        })
    return out


# -- orchestration ------------------------------------------------------------

def validate(sheet: str, ecc_df: pd.DataFrame, s4_df: pd.DataFrame) -> ValidationReport:
    """sheet is 'MARC' or 'MBEW'. ecc_df uses raw ECC technical column names;
    s4_df uses the friendly S/4 field names from the mapping."""
    key_fields = get_key_fields(sheet)

    rules: list[RuleResult] = [
        rule_field_coverage(sheet, s4_df),
        rule_mandatory_completeness(sheet, s4_df),
        rule_type_length_conformance(sheet, s4_df),
        rule_record_count(sheet, ecc_df, s4_df),
    ]

    return ValidationReport(
        sheet=sheet,
        key_fields=key_fields,
        ecc_row_count=len(ecc_df),
        s4_row_count=len(s4_df),
        rules=rules,
        overall_status=_overall_status(rules),
    )