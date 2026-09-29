"""
Validation service: proves that an ECC extract (raw SAP technical field
names, e.g. WERKS/DISMM/BKLAS) was correctly converted into its S/4 target
file (friendly field names, e.g. Plant/MRP Type/Valuation Class) according
to the rules encoded in MARC_MBEW_Mappings.xlsx.

Every rule below is *derived from the mapping*, not hardcoded business
knowledge - so a change to the workbook automatically changes what gets
checked. Six checks, run in order from "does the shape look right" down to
"does every value actually match":

  1. Field coverage         - every mapped S/4 field exists as a column
  2. Mandatory completeness - mandatory-for-sheet fields have no blanks
  3. Type & length          - values fit the mapped data type / length
  4. Record count            - row counts reconcile between ECC and S/4
  5. Key integrity           - business keys line up 1:1, no orphans/dupes
  6. Value transformation   - per-field values match across matched keys

Each rule returns a RuleResult; validate() bundles them into a
ValidationReport with an overall pass/fail so the UI can render a report
(the Validation page auto-loads the latest ECC/S4 file, per the existing
Fetch -> Process -> Validate -> Push flow).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

import pandas as pd

from app.services.mapping_loader import (
    FieldMapping,
    get_field_mappings,
    get_key_fields,
    get_mandatory_fields,
)

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


# -- column resolution helpers ----------------------------------------------

def _normalized_map(columns) -> dict[str, str]:
    return {str(c).strip().upper(): c for c in columns}


def _resolve_ecc_column(ecc_df: pd.DataFrame, fm: FieldMapping) -> str | None:
    """Find the ECC column for a mapped field, trying the technical field
    name first, then its derivation note (e.g. WERKS -> DERIVED_WERKS)."""
    norm = _normalized_map(ecc_df.columns)
    if fm.source_field and fm.source_field.upper() in norm:
        return norm[fm.source_field.upper()]
    if fm.source_field_note and fm.source_field_note.upper() in norm:
        return norm[fm.source_field_note.upper()]
    return None


def _resolve_s4_column(s4_df: pd.DataFrame, fm: FieldMapping) -> str | None:
    norm = _normalized_map(s4_df.columns)
    return norm.get(fm.s4_field.upper())


_KEY_SEP = "\u241F"  # unit separator - joins composite key parts into one string label


def _build_key_series(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    """Joins composite key columns into a single string label. Using a plain
    string (rather than a tuple) as the row label avoids pandas' ambiguous
    .loc[tuple, col] behaviour when an Index holds tuple objects."""
    return df[columns].astype(str).apply(lambda r: _KEY_SEP.join(v.strip() for v in r), axis=1)


def _split_key(key_label: str) -> tuple:
    return tuple(key_label.split(_KEY_SEP))


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


def rule_type_length_conformance(sheet: str, s4_df: pd.DataFrame) -> RuleResult:
    mappings = get_field_mappings(sheet)
    issues = []
    for fm in mappings:
        col = _resolve_s4_column(s4_df, fm)
        if col is None:
            continue
        series = s4_df[col]
        # length check (Text fields)
        if fm.data_type == "Text" and fm.length:
            too_long = series.astype(str).str.len() > int(fm.length)
            count = int(too_long.sum())
            if count:
                issues.append(
                    {"field": fm.s4_field, "issue": f"{count} value(s) exceed max length {int(fm.length)}"}
                )
        # type check (sampled - full column, capped reporting)
        type_errors = [v for v in series if _violates_type(v, fm.data_type)]
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
        summary="All field values conform to the mapped data type and length.",
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


# -- Rule 5: key integrity ----------------------------------------------------

def rule_key_integrity(
    sheet: str, ecc_df: pd.DataFrame, s4_df: pd.DataFrame
) -> tuple[RuleResult, pd.Series | None, pd.Series | None]:
    """Returns the rule result plus the resolved key series for both frames
    (reused by Rule 6) so we don't recompute key matching twice."""
    key_fields = get_key_fields(sheet)
    mappings = {fm.s4_field: fm for fm in get_field_mappings(sheet)}

    ecc_cols, s4_cols = [], []
    for kf in key_fields:
        fm = mappings.get(kf)
        ecc_col = _resolve_ecc_column(ecc_df, fm) if fm else None
        s4_col = _resolve_s4_column(s4_df, fm) if fm else None
        if ecc_col is None or s4_col is None:
            return (
                RuleResult(
                    rule_id="key_integrity",
                    name="Key Integrity",
                    status=RuleStatus.WARNING,
                    summary=f"Could not resolve key field '{kf}' in both files - skipped.",
                ),
                None,
                None,
            )
        ecc_cols.append(ecc_col)
        s4_cols.append(s4_col)

    ecc_keys = _build_key_series(ecc_df, ecc_cols)
    s4_keys = _build_key_series(s4_df, s4_cols)

    ecc_set, s4_set = set(ecc_keys), set(s4_keys)
    missing_in_s4 = list(ecc_set - s4_set)
    orphans_in_s4 = list(s4_set - ecc_set)
    dup_counts = s4_keys.value_counts()
    duplicates = list(dup_counts[dup_counts > 1].index)

    issues = []
    if missing_in_s4:
        issues.append(
            {"issue": "keys in ECC but missing from S/4", "count": len(missing_in_s4),
             "sample": [dict(zip(key_fields, _split_key(k))) for k in missing_in_s4[:5]]}
        )
    if orphans_in_s4:
        issues.append(
            {"issue": "keys in S/4 with no ECC source row", "count": len(orphans_in_s4),
             "sample": [dict(zip(key_fields, _split_key(k))) for k in orphans_in_s4[:5]]}
        )
    if duplicates:
        issues.append(
            {"issue": "duplicate keys in S/4 (should be unique)", "count": len(duplicates),
             "sample": [dict(zip(key_fields, _split_key(k))) for k in duplicates[:5]]}
        )

    if issues:
        result = RuleResult(
            rule_id="key_integrity",
            name="Key Integrity",
            status=RuleStatus.FAIL,
            summary=f"Key mismatch on {' + '.join(key_fields)}: {len(issues)} issue type(s) found.",
            details=issues,
        )
    else:
        result = RuleResult(
            rule_id="key_integrity",
            name="Key Integrity",
            status=RuleStatus.PASS,
            summary=f"Every {' + '.join(key_fields)} key matches 1:1 between ECC and S/4, no duplicates.",
        )
    return result, ecc_keys, s4_keys


# -- Rule 6: field-level value transformation accuracy -----------------------

def rule_value_transformation(
    sheet: str,
    ecc_df: pd.DataFrame,
    s4_df: pd.DataFrame,
    ecc_keys: pd.Series,
    s4_keys: pd.Series,
) -> RuleResult:
    key_fields = get_key_fields(sheet)
    mappings = [fm for fm in get_field_mappings(sheet) if fm.s4_field not in key_fields]

    ecc_indexed = ecc_df.set_index(ecc_keys)
    s4_indexed = s4_df.set_index(s4_keys)
    common_keys = ecc_indexed.index.intersection(s4_indexed.index)
    # keep this rule meaningful even with duplicate keys from Rule 5
    common_keys = common_keys[~common_keys.duplicated()]

    field_mismatches: dict[str, int] = {}
    sample_mismatches: list[dict] = []
    fields_checked = 0

    for fm in mappings:
        ecc_col = _resolve_ecc_column(ecc_df, fm)
        s4_col = _resolve_s4_column(s4_df, fm)
        if ecc_col is None or s4_col is None:
            continue
        fields_checked += 1
        ecc_vals = ecc_indexed.loc[common_keys, ecc_col].astype(str).str.strip()
        s4_vals = s4_indexed.loc[common_keys, s4_col].astype(str).str.strip()
        mismatch_mask = ecc_vals.values != s4_vals.values
        mismatch_count = int(mismatch_mask.sum())
        if mismatch_count:
            field_mismatches[fm.s4_field] = mismatch_count
            if len(sample_mismatches) < MAX_SAMPLE_ISSUES:
                bad_keys = common_keys[mismatch_mask][: MAX_SAMPLE_ISSUES - len(sample_mismatches)]
                for k in bad_keys:
                    sample_mismatches.append(
                        {
                            "key": dict(zip(key_fields, _split_key(k))),
                            "field": fm.s4_field,
                            "ecc_value": ecc_indexed.at[k, ecc_col],
                            "s4_value": s4_indexed.at[k, s4_col],
                        }
                    )

    if field_mismatches:
        return RuleResult(
            rule_id="value_transformation",
            name="Field-Level Value Transformation Accuracy",
            status=RuleStatus.FAIL,
            summary=(
                f"{len(field_mismatches)} of {fields_checked} checked field(s) have mismatched "
                f"values across {len(common_keys)} matched record(s)."
            ),
            details=[{"field": f, "mismatch_count": c} for f, c in field_mismatches.items()]
            + [{"sample_mismatches": sample_mismatches}],
        )
    return RuleResult(
        rule_id="value_transformation",
        name="Field-Level Value Transformation Accuracy",
        status=RuleStatus.PASS,
        summary=f"All {fields_checked} checked field(s) match exactly across {len(common_keys)} matched record(s).",
    )


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

    key_result, ecc_keys, s4_keys = rule_key_integrity(sheet, ecc_df, s4_df)
    rules.append(key_result)

    if ecc_keys is not None and s4_keys is not None:
        rules.append(rule_value_transformation(sheet, ecc_df, s4_df, ecc_keys, s4_keys))
    else:
        rules.append(
            RuleResult(
                rule_id="value_transformation",
                name="Field-Level Value Transformation Accuracy",
                status=RuleStatus.WARNING,
                summary="Skipped - key fields could not be resolved (see Key Integrity).",
            )
        )

    if any(r.status == RuleStatus.FAIL for r in rules):
        overall = RuleStatus.FAIL
    elif any(r.status == RuleStatus.WARNING for r in rules):
        overall = RuleStatus.WARNING
    else:
        overall = RuleStatus.PASS

    return ValidationReport(
        sheet=sheet,
        key_fields=key_fields,
        ecc_row_count=len(ecc_df),
        s4_row_count=len(s4_df),
        rules=rules,
        overall_status=overall,
    )