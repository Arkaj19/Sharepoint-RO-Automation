"""
Reading extract files into DataFrames. Everything is read as text
(dtype=str, no NaN conversion), so '000123' keeps its zeros and an empty
cell stays an empty string - the diff and validation compare exact text.
"""
from __future__ import annotations

import io
import os

import pandas as pd

LINEAGE_PREFIX = "__"
SRC_FILE_COL = "__src_file"
SRC_PART_COL = "__src_part"


def read_bytes(content: bytes, reader: str) -> pd.DataFrame:
    buf = io.BytesIO(content)
    if reader == "excel":
        return pd.read_excel(buf, dtype=str, keep_default_na=False, engine="openpyxl")
    return pd.read_csv(buf, dtype=str, keep_default_na=False, low_memory=False)


def read_table(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path, dtype=str, keep_default_na=False)
    return pd.read_csv(path, dtype=str, keep_default_na=False, low_memory=False)


def read_upload(filename: str, content: bytes) -> pd.DataFrame:
    ext = os.path.splitext(filename or "")[1].lower()
    return read_bytes(content, "excel" if ext in (".xlsx", ".xls") else "csv")


def data_columns(df: pd.DataFrame) -> list[str]:
    """Columns that came from the extract (lineage columns excluded)."""
    return [c for c in df.columns if not str(c).startswith(LINEAGE_PREFIX)]
