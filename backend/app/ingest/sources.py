"""
Which files make up each dataset.

A dataset is one (object, side) pair:
  * migration objects - MARC/ECC, MARC/S4, MBEW/ECC, MBEW/S4;
  * supporting tables - REF/MARA, REF/MARM, REF/MARD, ... used by lookups
    in the mapping rules, plus the business rule workbook (REF/RULE_WORKBOOK).

Files are looked for on SharePoint first. Supporting tables that aren't on
SharePoint are also picked up from the local drop folder
backend/data/incoming/ (same filename rules), so a table someone hands over
by other means still gets versioned like everything else. Optional tables
that are missing everywhere are reported as "missing" - the rules that need
them then report "input missing" instead of failing.

Adding a migration object means adding its two rows here plus a
mappings/<OBJECT>.yaml file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.core.config import settings

SourceSide = str
REF = "REF"


@dataclass(frozen=True)
class SourceSpec:
    object: str
    side: SourceSide
    folder_setting: str               # name of the Settings attribute holding the folder path
    prefix: str                       # filename prefix (case-insensitive for supporting tables)
    extensions: tuple[str, ...]       # lower-case extensions
    reader: Literal["csv", "excel", "auto"]
    required: bool = True
    allow_local: bool = False         # also look in data/incoming/
    description: str = ""

    @property
    def folder(self) -> str:
        return getattr(settings, self.folder_setting)

    @property
    def dataset(self) -> str:
        return f"{self.object}/{self.side}"

    def matches(self, filename: str) -> bool:
        name_ok = (filename.lower().startswith(self.prefix.lower()) if self.object == REF
                   else filename.startswith(self.prefix))
        return name_ok and filename.lower().endswith(self.extensions)

    def reader_for(self, filename: str) -> str:
        if self.reader != "auto":
            return self.reader
        return "excel" if filename.lower().endswith((".xlsx", ".xlsm")) else "csv"


SOURCES: list[SourceSpec] = [
    SourceSpec("MARC", "ECC", "ECC_FOLDER_PATH", "MARC_DAP", (".xlsx",), "excel"),
    SourceSpec("MARC", "S4", "FOLDER_PATH", "S_MARC#FreeText", (".csv",), "csv"),
    SourceSpec("MBEW", "ECC", "ECC_FOLDER_PATH", "MBEW_DAP", (".xlsx",), "excel"),
    SourceSpec("MBEW", "S4", "FOLDER_PATH", "S_MBEW#FreeText", (".csv",), "csv"),
    # supporting tables for lookups
    SourceSpec(REF, "MARA", "ECC_FOLDER_PATH", "MARA_DAP", (".xlsx", ".csv"), "auto", False, True,
               "Material master: material type, base unit, product hierarchy, deletion flag"),
    SourceSpec(REF, "MARM", "ECC_FOLDER_PATH", "MARM_DAP", (".xlsx", ".csv"), "auto", False, True,
               "Units of measure: EA-per-case conversion"),
    SourceSpec(REF, "MARD", "ECC_FOLDER_PATH", "MARD_DAP", (".xlsx", ".csv"), "auto", False, True,
               "Storage locations (plants 1000 / 1029 routing)"),
    SourceSpec(REF, "MVKE", "ECC_FOLDER_PATH", "MVKE_DAP", (".xlsx", ".csv"), "auto", False, True,
               "Sales data: completes the material scope (S_MARA)"),
    SourceSpec(REF, "EKGRP_UPDATED", "ECC_FOLDER_PATH", "ekgrp_updated", (".xlsx", ".csv"), "auto", False, True,
               "Purchasing-group overrides per material and plant"),
    SourceSpec(REF, "MATNR_1025", "ECC_FOLDER_PATH", "1025_new_matnr", (".xlsx", ".csv"), "auto", False, True,
               "Material renumbering for plant 1025 (MATNR_new)"),
    SourceSpec(REF, "RULE_WORKBOOK", "ECC_FOLDER_PATH", "Material-Master_Source", (".xlsx",), "excel", False, True,
               "Business rule workbook (field logic per S/4 structure)"),
]

OBJECTS: list[str] = sorted({s.object for s in SOURCES if s.object != REF})
SIDES: tuple[str, ...] = ("ECC", "S4")
REF_TABLES: list[str] = [s.side for s in SOURCES if s.object == REF]

_PART_RE = re.compile(r"-\s*([^-]+?)\s*\.[A-Za-z0-9]+$")


def part_label(filename: str) -> str:
    """'S_MARC#FreeText - 1021.csv' -> '1021'. The S/4 extract is split per
    source plant, so this label is strong evidence for the plant crosswalk."""
    m = _PART_RE.search(filename)
    return m.group(1) if m else filename.rsplit(".", 1)[0]


def get_source(obj: str, side: str) -> SourceSpec:
    for s in SOURCES:
        if s.object == obj and s.side == side:
            return s
    raise KeyError(f"Unknown dataset {obj}/{side}")
