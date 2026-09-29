"""
Small file-loading helpers shared by the validation route: reading a CSV or
Excel file into a DataFrame regardless of which one was dropped, and finding
the most recently modified file in a directory that matches a sheet name.
"""
from __future__ import annotations

import glob
import os

import pandas as pd


class TableFileNotFound(FileNotFoundError):
    pass


def read_table(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path, dtype=str)
    return pd.read_csv(path, dtype=str)


def find_latest(directory: str, sheet: str) -> str:
    """Finds the most recently modified CSV/Excel file in `directory` whose
    name contains `sheet` (case-insensitive), e.g. sheet='MARC' matches
    'MARC_combined.csv' or 'S_MARC#FreeText - part1.csv'."""
    if not os.path.isdir(directory):
        raise TableFileNotFound(f"Directory not found: {directory}")

    candidates = []
    for ext in ("*.csv", "*.xlsx", "*.xls"):
        candidates.extend(glob.glob(os.path.join(directory, ext)))

    matches = [c for c in candidates if sheet.lower() in os.path.basename(c).lower()]
    if not matches:
        raise TableFileNotFound(
            f"No file matching sheet '{sheet}' found in {directory}."
        )
    return max(matches, key=os.path.getmtime)

 