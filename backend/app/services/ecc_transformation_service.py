"""
Builds the *Expected S/4* dataframe from a raw ECC extract.

    raw ECC dataframe ──► normalise headers / values ──► ecc_to_s4 engine ──► Expected S/4

Why a separate layer around ``ecc_to_s4``:

* The ECC files fetched from SharePoint carry SAP *display labels* as headers
  ("Material", "Plant", "Procurement type" ...), while the transformation logic
  works on technical field names (MATNR, WERKS, BESKZ ...).  ``ECC_LABELS``
  translates one into the other.  Files that already use technical names pass
  through untouched.
* The transformation (like the Databricks views it replicates) also needs
  reference tables that are not part of the MARC/MBEW extract itself:
  MARA, MARM (required) and the optional lookup EKGRP_UPDATED (MARC only).
  They are picked up from the same folder
  convention the fetch step already uses:

      {OUTPUT_DIR}/{TABLE}/ECC/{TABLE}_ECC_combined.(csv|xlsx)

  For the MBEW sheet the latest MARC ECC file is also loaded, because the
  MBEW views derive the plant of the split plants (1021/1028/1030) from MARC.
"""
from __future__ import annotations

import logging
import os
import re

import numpy as np
import pandas as pd

from app.core.config import settings
from app.services import ecc_to_s4 as engine
from app.services.table_io import TableFileNotFound, find_latest, read_table

log = logging.getLogger("ecc_transformation")


class ReferenceDataMissing(RuntimeError):
    """A table the transformation cannot run without is not available."""


# --------------------------------------------------------------------------- #
# ECC display label -> technical field.
#
# VERIFIED  = confirmed against the data (plant 1025 -> US29 is a 1:1 pass-through
#             in the SQL, so the ECC column must equal the S/4 column).
# INFERRED  = constant/blank in the sample data so it cannot be verified there;
#             taken from the SAP table field order.  Check these against DD03T
#             if a mismatch shows up on one of them.
# A label that is absent from the file is simply left NULL (and logged).
# --------------------------------------------------------------------------- #
MARC_LABELS: dict[str, str] = {
    # keys / flags
    "Material": "MATNR", "Plant": "WERKS", "DF at plant level": "LVORM",
    # VERIFIED
    "Procurement type": "BESKZ", "Special procurement": "SOBSL", "Prod.Sched.Profile": "SFCPF",
    "Storage loc. for EP": "LGFSB", "Prod. stor. location": "LGPRO", "MRP Type": "DISMM",
    "MRP Controller": "DISPO", "Plant-sp.matl status": "MMSTA", "Valid from": "MMSTD",
    "Unit of issue": "AUSME", "Effective-out date": "AUSDT", "Follow-Up Material": "NFMAT",
    "Ordering costs": "LOSFX", "Costing Lot Size": "LOSGR", "Variance Key": "AWSLS",
    "Production unit": "FRTME", "Prodn Supervisor": "FEVOR", "Minimum Lot Size": "BSTMI",
    "Maximum Lot Size": "BSTMA", "Fixed lot size": "BSTFE", "Rounding value": "BSTRF",
    "Batch management.1": "XCHPF", "Batch entry": "KZECH", "Country of origin": "HERKL",
    "Source list": "KORDB", "Automatic PO": "KAUTB", "Lot size": "DISLS", "Safety Stock": "EISBE",
    "Individual/coll.": "SBDKZ", "Component scrap (%)": "KAUSF", "In-house production": "DZEIT",
    "GR processing time": "WEBAZ", "Backflush": "RGEKZ", "SchedMargin key": "FHORI",
    "Reset automatically": "AUTRU", "Overdely tolerance": "UEETO", "Underdely tolerance": "UNETO",
    "Unltd Overdelivery": "UEETK", "CC phys. inv. ind.": "ABCIN", "Do Not Cost": "NCOST",
    "Max. Storage Period": "MAXLZ", "Time unit": "LZEIH", "Planned Deliv. Time": "PLIFZ",
    "Loading Group": "LADGR", "Profit Center": "PRCTR",
    # INFERRED
    "Assembly scrap (%)": "AUSSS", "Reorder Point": "MINBE", "Maximum stock level": "MABST",
    "Storage costs ind.": "LAGPR", "Mixed MRP": "MISKZ", "Requirements group": "KZBED",
    "Discontinuation ind.": "KZAUS", "Processing time": "BEARZ", "Setup time": "RUEZT",
    "Interoperation": "TRANZ", "Base quantity": "BASMG", "Tot. repl. lead time": "WZEIT",
    "Post to insp. stock": "INSMK", "Documentation reqd": "KZDKZ", "Inspection interval": "PRFRQ",
    "Service level (%)": "LGRAD", "Splitting indicator": "AUFTL", "Fiscal Year Variant": "PERIV",
    "Correction factors": "KZKFK", "Setup time.1": "VRVEZ", "Base quantity.1": "VBAMG",
    "Processing time.1": "VBEAZ", "Source of supply": "BWSCL", "Critical Part": "KZKRI",
    "ABC Indicator": "MAABC", "Export/import group": "MTVER", "Region of origin": "HERKR",
    "Comm./imp. code no.": "STEUC", "Mat. CFOP category": "INDUS", "Repetitive mfg": "SAUFT",
    "REM profile": "SFEPR", "MRP group": "DISGR", "Takt time": "TAKZT", "Coverage profile": "RWPRO",
    "Neg. stocks in plant": "XMCNG", "Stock determ. group": "EPRIO", "Co-product": "KZKUP",
    "QM material auth.": "QMATA", "QM Control Key": "SSQSS", "Certificate type": "QZGTP",
    "Target QM system": "QSSYS", "Planning cycle": "LFRHY", "Rounding Profile": "RDPRF",
    "RefMatl: consumption": "VRBMT", "RefPlant:consumption": "VRBWK", "Date to": "VRBDT",
    "Multiplier": "VRBFK", "Planning calendar": "MRPPP", "Min safety stock": "EISLO",
    "Safety time/act.cov.": "SHZET", "Safety time ind.": "SHFLG", "Bulk material": "SCHGT",
    "CC indicator fixed": "CCFIX", "Serial no. profile": "SERNP", "Unit of Measure Grp": "MEGRU",
    "Production Version": "FPRFM", "GI Proc. Time": "GI_PR_TIME",
}

MBEW_LABELS: dict[str, str] = {
    "Material": "MATNR", "Valuation Area": "BWKEY", "Valuation Type": "BWTAR",
    "Del. flag val. type": "LVORM",
    # VERIFIED
    "Valuation Category": "BWTTY", "Price control": "VPRSV", "Standard price": "STPRS",
    "Price unit": "PEINH", "Planned price 2": "ZPLP2", "Planned price date 2": "ZPLD2",
    "Overhead Group": "KOSGR", "Valid from": "ZKDAT",
    # INFERRED
    "Moving price": "VERPR", "Valuation Class": "BKLAS", "Future price": "ZKPRS",
    "Planned price 1": "ZPLP1", "Planned price 3": "ZPLP3", "Planned price date 1": "ZPLD1",
    "Planned price date 3": "ZPLD3", "Tax price 1": "BWPRS", "Commercial price 1": "BWPS1",
    "Tax price 3": "VJBWS", "Commercial price 3": "BWPEI", "Total val. YBL": "BWPRH",
    "Total stock in YBL": "BWPH1", "Total stock in PBL": "VJBWH", "Devaluation Ind.": "ABWKZ",
    "Valuation margin": "BWSPA", "Material price determination: control": "MLAST",
    "Proj. stk val. class": "QKLAS", "LIFO/FIFO-relevant": "XLIFO", "LIFO Pool": "MYPOL",
    "Material usage": "MTUSE", "Material origin.1": "MTORG", "Origin Group": "HRKFT",
}

# Reference tables: several plausible export headers per technical field (first hit wins).
MARA_ALIASES = {
    "MATNR": ["Material"], "LVORM": ["Deletion Flag", "DF at client level", "Flag Material for Deletion at Client Level"],
    "MTART": ["Material Type", "Mat. type"], "MEINS": ["Base Unit of Measure", "Base Unit"],
    "PRDHA": ["Prod.hierarchy", "Product hierarchy", "Product Hierarchy"],
}
MARM_ALIASES = {
    "MATNR": ["Material"], "MEINH": ["Alt. Unit of Measure", "Alternative Unit of Measure", "AUn"],
    "UMREN": ["Denominator"],
}
EKGRP_ALIASES = {"MATNR": ["Material", "PRODUCT"], "WERKS": ["Plant"], "EKGRP": ["Purchasing Group"]}

_FLOAT_INT = re.compile(r"^(-?\d+)\.0+$")


# --------------------------------------------------------------------------- #
# Cleaning / normalisation
# --------------------------------------------------------------------------- #
def _clean_cell(v):
    if not isinstance(v, str):
        return v
    t = v.strip()
    if t == "":
        return np.nan
    m = _FLOAT_INT.match(t)          # '40.0' -> '40' (float artefact of CSV round-trips)
    return m.group(1) if m else t


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    for c in df.columns:
        df[c] = df[c].astype(object).map(_clean_cell)
    return df


def _rename(df: pd.DataFrame, table: str, label_map: dict[str, str] | None = None,
            aliases: dict[str, list[str]] | None = None) -> pd.DataFrame:
    """Translate ECC display labels to technical names.  Columns that are already
    technical (upper-case names the engine knows) are kept as they are."""
    df = _clean(df)
    wanted = set(engine.REQUIRED_COLS[table])
    upper = {c.upper(): c for c in df.columns}
    ren: dict[str, str] = {}
    if label_map:
        ci = {k.upper(): v for k, v in label_map.items()}
        for c in df.columns:
            if c.upper() in ci and c.upper() not in {w for w in wanted if w in upper}:
                ren[c] = ci[c.upper()]
    if aliases:
        for tech, names in aliases.items():
            if tech in df.columns:
                continue
            for n in names:
                if n.upper() in upper:
                    ren[upper[n.upper()]] = tech
                    break
    # already-technical columns
    for c in df.columns:
        if c not in ren and c.upper() in wanted and c != c.upper():
            ren[c] = c.upper()
    df = df.rename(columns=ren)
    # if a label map produced the same technical name twice keep the first one
    df = df.loc[:, ~df.columns.duplicated()]
    missing = [c for c in engine.REQUIRED_COLS[table] if c not in df.columns]
    if missing:
        log.warning("%s: %d field(s) have no source column in the ECC file and are treated as NULL: %s",
                    table, len(missing), ", ".join(missing[:30]) + (" ..." if len(missing) > 30 else ""))
        for c in missing:
            df[c] = np.nan
    if "MATNR" in df.columns:
        df["MATNR"] = engine.strip_zeros(df["MATNR"])
    return df.reset_index(drop=True)


def normalize_ecc(sheet: str, raw: pd.DataFrame) -> pd.DataFrame:
    """Public: raw ECC dataframe (display labels or technical names) -> technical names."""
    return _rename(raw, sheet, MARC_LABELS if sheet == "MARC" else MBEW_LABELS)


# --------------------------------------------------------------------------- #
# Reference tables
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Reference tables
# --------------------------------------------------------------------------- #
_REF_CACHE: dict = {}


def _load_ref(name: str, folder: str, table_key: str, aliases: dict, required: bool):
    d = os.path.join(settings.OUTPUT_DIR, folder, "ECC")
    try:
        path = find_latest(d, f"{folder}_ECC_combined")
    except TableFileNotFound as exc:
        if required:
            raise ReferenceDataMissing(
                f"{name} is required to transform ECC -> S/4 but no file was found. "
                f"Expected {os.path.join(d, folder + '_ECC_combined.csv')} (or .xlsx).'"
            ) from exc
        log.warning("Optional reference table %s not found in %s", name, d)
        return None

    key = (path, os.path.getmtime(path))
    if key in _REF_CACHE:
        return _REF_CACHE[key]
    pkl = path + ".refcache.pkl"
    if os.path.exists(pkl) and os.path.getmtime(pkl) >= key[1]:
        df = pd.read_pickle(pkl)
    else:
        df = _rename(read_table(path), table_key, aliases=aliases)
        df = df[engine.REQUIRED_COLS[table_key]]
        df.to_pickle(pkl)
    _REF_CACHE[key] = df
    log.info("Loaded reference %-10s %8d rows (%s)", name, len(df), os.path.basename(path))
    return df
def _load_ref(name: str, folder: str, table_key: str, aliases: dict, required: bool):
    d = os.path.join(settings.OUTPUT_DIR, folder, "ECC")
    try:
        path = find_latest(d, f"{folder}_ECC_combined")
    except TableFileNotFound as exc:
        if required:
            raise ReferenceDataMissing(
                f"{name} is required to transform ECC -> S/4 but no file was found. "
                f"Expected {os.path.join(d, folder + '_ECC_combined.csv')} (or .xlsx)."
            ) from exc
        log.warning("Optional reference table %s not found in %s", name, d)
        return None
    # df = _rename(read_table(path), table_key, aliases=aliases)
    key = (path, os.path.getmtime(path))
    if key in _REF_CACHE:                                  # in-memory: free after first call
        return _REF_CACHE[key]
    pkl = path + ".refcache.pkl"                           # on disk: survives restarts
    if os.path.exists(pkl) and os.path.getmtime(pkl) >= key[1]:
        df = pd.read_pickle(pkl)
    else:                                                  # slow path: once per file change
        df = _rename(read_table(path), table_key, aliases=aliases)
        df = df[engine.REQUIRED_COLS[table_key]]           # drop the ~200 columns the engine never reads
        df.to_pickle(pkl)
    _REF_CACHE[key] = df
    log.info("Loaded reference %-10s %8d rows (%s)", name, len(df), os.path.basename(path))
    return df


def load_reference_tables() -> dict:
    return {
        "MARA": _load_ref("MARA", "MARA", "MARA", MARA_ALIASES, True),
        "MARM": _load_ref("MARM", "MARM", "MARM", MARM_ALIASES, True),
        "EKGRP": _load_ref("EKGRP_UPDATED", "EKGRP_UPDATED", "EKGRP", EKGRP_ALIASES, False),
    }


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def build_expected_s4(sheet: str, ecc_df: pd.DataFrame, strict_plant_join: bool = False) -> pd.DataFrame:
    """Raw ECC dataframe for `sheet` ('MARC'|'MBEW')  ->  Expected S/4 dataframe.

    Raises ReferenceDataMissing if MARA / MARM (or, for MBEW, the MARC extract) are unavailable.
    """
    if sheet not in ("MARC", "MBEW"):
        raise ValueError(f"Unsupported sheet '{sheet}'")
    T = load_reference_tables()
    own = normalize_ecc(sheet, ecc_df)
    if sheet == "MARC":
        T["MARC"] = own
        T["MBEW"] = pd.DataFrame(columns=engine.REQUIRED_COLS["MBEW"])
    else:
        T["MBEW"] = own
        marc_dir = os.path.join(settings.OUTPUT_DIR, "MARC", "ECC")
        try:
            marc_raw = read_table(find_latest(marc_dir, "MARC_ECC_combined"))
        except TableFileNotFound as exc:
            raise ReferenceDataMissing(
                f"The MARC ECC extract is required to derive the MBEW plants but none was found in {marc_dir}."
            ) from exc
        T["MARC"] = normalize_ecc("MARC", marc_raw)
    engine.STRICT_DATES = False
    expected = engine.transform_table(T, sheet, strict_join=strict_plant_join)
    log.info("Expected S/4 %s: %d rows from %d raw ECC rows", sheet, len(expected), len(ecc_df))
    return expected