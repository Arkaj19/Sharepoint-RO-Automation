"""
Column profiles of a dataset version. Computed once when a version is
stored (profile.json) and reused by the diff engine and Agent 1's tools.

Per column: null (blank) rate, distinct count, min/max length, inferred
type, the most common character patterns, and - for low-cardinality "code"
columns such as plant, MRP type or procurement type - the value counts.
"""
from __future__ import annotations

import re

import pandas as pd

from app.ingest.readers import data_columns

CODE_MAX_DISTINCT = 50         # a column with at most this many values is a "code"
TOP_VALUES = 15

_INT_RE = re.compile(r"^-?\d+$")
_DEC_RE = re.compile(r"^-?\d*[.,]\d+$|^-?\d+[.,]\d*$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}|\d{2}/\d{2}/\d{4})( \d{2}:\d{2}:\d{2})?$")


def _pattern(value: str) -> str:
    """'US30' -> 'A9', '000000009687900109' -> '9'. Runs are collapsed."""
    p = re.sub(r"[A-Za-z]", "A", value)
    p = re.sub(r"\d", "9", p)
    return re.sub(r"(.)\1+", r"\1", p)


def infer_type(non_blank: pd.Series) -> str:
    if non_blank.empty:
        return "empty"
    sample = non_blank.drop_duplicates().head(2000)
    if sample.str.match(_INT_RE).all():
        return "int"
    if sample.str.match(_INT_RE.pattern + "|" + _DEC_RE.pattern).all():
        return "decimal"
    if sample.str.match(_DATE_RE).all():
        return "date"
    return "text"


def profile_series(series: pd.Series) -> dict:
    vals = series.fillna("").astype(str).str.strip()
    n = len(vals)
    non_blank = vals[vals != ""]
    lengths = non_blank.str.len()
    counts = non_blank.value_counts()
    distinct = int(counts.size)
    prof = {
        "rows": n,
        "blank": int(n - len(non_blank)),
        "blank_rate": round((n - len(non_blank)) / n, 4) if n else 0.0,
        "distinct": distinct,
        "min_len": int(lengths.min()) if distinct else 0,
        "max_len": int(lengths.max()) if distinct else 0,
        "type": infer_type(non_blank),
        "is_code": 0 < distinct <= CODE_MAX_DISTINCT,
        "patterns": (non_blank.drop_duplicates().head(5000).map(_pattern)
                     .value_counts().head(3).to_dict() if distinct else {}),
    }
    if prof["is_code"]:
        prof["values"] = {str(k): int(v) for k, v in counts.items()}
    else:
        prof["top_values"] = {str(k): int(v) for k, v in counts.head(5).items()}
    return prof


def profile_frame(df: pd.DataFrame) -> dict:
    cols = data_columns(df)
    return {
        "rows": int(len(df)),
        "columns": cols,
        "profiles": {str(c): profile_series(df[c]) for c in cols},
    }
