"""
Loads the ECC -> S/4 field mapping (MARC_MBEW_Mappings.xlsx, pre-parsed into
app/data/marc_mbew_mapping.json) and exposes it as typed lookups the
validation service can query.

The mapping is the single source of truth for what "correctly converted"
means: which ECC table/field feeds which S/4 friendly field, its expected
data type / length / decimal precision, and whether it's mandatory or part
of the business key for that sheet (MARC = plant data, MBEW = valuation
data).

If the mapping is ever updated in the source workbook, regenerate the JSON
with `python -m app.services.mapping_loader --rebuild` (re-parses the .xlsx
in app/data/ using the same column layout: B=group, C=S4 field label,
D=mandatory flag, E=data type, F=length, G=decimal, H=sheet, I=source
table, J=source field).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MAPPING_JSON_PATH = DATA_DIR / "marc_mbew_mapping.json"
MAPPING_XLSX_PATH = DATA_DIR / "MARC_MBEW_Mappings.xlsx"


@dataclass(frozen=True)
class FieldMapping:
    s4_field: str            # friendly S/4 column name, e.g. "Plant"
    group: str | None        # UI grouping in the source workbook
    mandatory: bool          # flagged "mandatory for sheet"
    is_key_marker: bool      # explicitly marked "Key" in the workbook
    data_type: str | None    # "Text" | "Number" | "Date"
    length: float | None     # max length (Text) / total digits (Number)
    decimal: float | None    # decimal places (Number only)
    sheet: str               # "S_MARC" | "S_MBEW"
    source_table: str | None  # ECC table the field is sourced from
    source_field: str | None  # ECC technical field name, e.g. "WERKS"
    source_field_note: str | None  # e.g. "DERIVED_WERKS"


# Business key definition per sheet. Not every key component is marked
# "Key" in the workbook (only Product Number is) - Plant / Valuation Area
# are the second key component by convention (plant-level / valuation-area
# level master data), identified here as the field immediately following
# the key field that is also flagged mandatory.
_SECONDARY_KEY_FIELD = {
    "MARC": "Plant",
    "MBEW": "Valuation Area",
}


@lru_cache(maxsize=None)
def _load_raw() -> dict:
    with open(MAPPING_JSON_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def get_field_mappings(sheet: str) -> list[FieldMapping]:
    """sheet is 'MARC' or 'MBEW'."""
    raw = _load_raw()
    if sheet not in raw:
        raise KeyError(f"No mapping loaded for sheet '{sheet}'. Known: {list(raw)}")
    return [FieldMapping(**f) for f in raw[sheet]["fields"]]


@lru_cache(maxsize=None)
def get_key_fields(sheet: str) -> list[str]:
    fields = get_field_mappings(sheet)
    primary = next((f.s4_field for f in fields if f.is_key_marker), None)
    secondary = _SECONDARY_KEY_FIELD.get(sheet)
    keys = [k for k in (primary, secondary) if k]
    if not keys:
        raise ValueError(f"Could not determine key fields for sheet '{sheet}'.")
    return keys


@lru_cache(maxsize=None)
def get_mandatory_fields(sheet: str) -> list[str]:
    return [f.s4_field for f in get_field_mappings(sheet) if f.mandatory]


def get_condition_notes(sheet: str) -> str | None:
    raw = _load_raw()
    return raw.get(sheet, {}).get("condition_notes")


# -- one-off rebuild helper (not used at request time) ----------------------

def _rebuild_from_xlsx() -> None:
    import openpyxl

    wb = openpyxl.load_workbook(MAPPING_XLSX_PATH, data_only=True)
    result: dict = {}
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        fields = []
        condition_text = None
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=True):
            row = row + (None,) * (10 - len(row))
            a, b, c, d, e, f, g, h, i_, j = row[:10]
            if a and "INNER CONDITION" in str(a):
                condition_text = a
            if not j or not c:
                continue
            raw_field = str(j).strip()
            m = re.match(r"^([A-Z0-9_]+)", raw_field)
            base_field = m.group(1) if m else raw_field
            pm = re.search(r"\(([^)]+)\)", raw_field)
            note = pm.group(1).strip() if pm else None
            fields.append(
                {
                    "s4_field": str(c).strip(),
                    "group": str(b).strip() if b else None,
                    "mandatory": bool(d and "mandatory" in str(d).lower()),
                    "is_key_marker": bool(b and str(b).strip().lower() == "key"),
                    "data_type": str(e).strip() if e else None,
                    "length": f if isinstance(f, (int, float)) else None,
                    "decimal": g if isinstance(g, (int, float)) else None,
                    "sheet": str(h).strip() if h else sheet_name,
                    "source_table": str(i_).strip() if i_ else None,
                    "source_field": base_field,
                    "source_field_note": note,
                }
            )
        result[sheet_name] = {"condition_notes": condition_text, "fields": fields}

    with open(MAPPING_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)


if __name__ == "__main__":
    import sys

    if "--rebuild" in sys.argv:
        _rebuild_from_xlsx()
        print(f"Rebuilt {MAPPING_JSON_PATH} from {MAPPING_XLSX_PATH}")
