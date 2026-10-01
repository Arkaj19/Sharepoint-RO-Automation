"""
ECC -> S/4 transformation engine for MARC / MBEW.

This is the transformation half of ``ecc_to_s4_marc_mbew.py``, moved into the
backend unchanged in behaviour: it replicates the Databricks views
con_dev.s4_mdm.mm_S_MARC / mm_S_MBEW (Transformation_Rules.txt), including
their quirks, so its output can be used as the *Expected* S/4 dataset.

Only the CLI, the file I/O and the comparison code of the original script were
dropped - those concerns now live in ``ecc_transformation_service`` (I/O,
header normalisation) and ``validation_service`` (comparison).

Inputs are DataFrames of **technical** SAP column names (MATNR, WERKS, ...),
all string typed, blank == NULL.

RULE-BOOK ALIGNMENT (MARC)
--------------------------
The MARC output follows MARC_Mappings_Rule_Book.xlsx only.  Logic that existed in
the Databricks SQL but is not in the rule book was removed for MARC: the MATNR_new
output column / NEW_MATNR lookup, the S_MARA material filter, the DISMM 'ND' and
MMSTA 'ZP' rules for BESKZ = 'X', PRCTR = derived plant (now passthrough), the
storage-location and MARD conditions in the plant derivation, and the SOBSL /
LGPRO / LGFSB rules for plants the rule book does not cover.

RULE-BOOK ALIGNMENT (MBEW)
--------------------------
The MBEW output follows MBEW_Mappings.xlsx only.  Compared with the Databricks
view, the following were removed / changed for MBEW:

* filters: MARA.LVORM IS NULL, MARA.MTART <> 'NVAL' (all plant groups),
  MARC.LVORM IS NULL (split plants), MBEW.LVORM IS NULL and
  MBEW.MATNR in MARA (LVORM IS NULL, MTART <> 'UNBW').  The S_MARA material
  filter and the MATNR_new output column / NEW_MATNR lookup are gone.
* plants: 1025 -> US29, 1029 -> CA02, 1000 -> US26 as a plain mapping (no MARD /
  storage-location condition, so no MATNR-only fan-out for 1000/1029).
* KOSGR: DERIVED_WERKS in (CA02, US30, US31, US33) -> '' else KOSGR (the 1021
  BAKI/DAKI/PAKI override via MARC.SFCPF is gone).
* STPRS (FERT + MEINS = 'CS'): STPRS / MARM_EA.UMREN rounded to 2 decimals.

QUIRKS replicated from the SQL
------------------------------
* ZPLD1 (a date field) is filled with cast(STPRS as string), same as ZPLP1.
* LGFSB's ELSE branch in 1021/1028/1030 returns MARC.LGPRO, not MARC.LGFSB.
* AWSLS tests LOSGR = '0.000' (not AWSLS).
"""
from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import numpy as np
import pandas as pd

log = logging.getLogger("ecc_to_s4")
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

# ZPLD2/ZPLD3: True => accept only MM/dd/yyyy like the SQL (default: also other formats)
STRICT_DATES = False

# --------------------------------------------------------------------------- #
# 1. Constants / plant profiles
# --------------------------------------------------------------------------- #
DAP_PLANTS = ("US30", "US31", "US33", "CA02")   # plants with the "blank out" rules
SP_PLANTS = ("US30", "US31", "US33")            # MTVFP = 'SP', BESKZ E -> F
# Rule book: 1000 -> US26, 1029 -> CA02 (no MARD / storage-location condition)
OTHER_PLANT_MAP = {"1000": "US26", "1029": "CA02"}


@dataclass(frozen=True)
class Profile:
    label: str
    src: tuple                 # ECC plants covered
    mode: str                  # 'split' (1021/1028/1030) | 'single' (1025) | 'other' (1000/1029)
    plant_x: str = ""          # split: target for DAP3 / externally procured
    plant_y: str = ""          # split: target for in-house
    target: str = ""           # single: target plant
    mmsta_x_zp: bool = False   # 1021 only: BESKZ = 'X' -> MMSTA 'ZP'


PROFILES = [
    Profile("1021", ("1021",), "split", "US30", "US27", mmsta_x_zp=True),
    Profile("1028", ("1028",), "split", "US31", "US28"),
    Profile("1030", ("1030",), "split", "US33", "US32"),
    Profile("1025", ("1025",), "single", target="US29"),
    Profile("1000_1029", ("1000", "1029"), "other"),
]

# SOBSL mapping by derived plant - rule book defines US30 only ('E' = BESKZ 'E').
SOBSL_MAP = {
    "US30": {"40": "64", "41": "67", "42": "6A", "E": "62"},
}

MARC_COLUMNS = [
    "PRODUCT", "WERKS", "DISMM", "DISPO", "MTVFP", "MMSTA", "MMSTD", "KOKRS", "PRCTR",
    "AUSME", "XCHPF", "SERNP", "XMCNG", "EPRIO", "LADGR", "KZKUP", "KZECH", "KZKRI", "QMATA", "KZDKZ",
    "PRFRQ", "SSQSS", "QZGTP", "QSSYS", "MTVER", "HERKL", "HERKR", "STEUC", "INDUS", "EKGRP", "INSMK",
    "KORDB", "KAUTB", "TAXIM", "MAABC", "DISGR", "STRGR", "MINBE", "FXHOR", "LFRHY", "VRMOD", "VINT1",
    "VINT2", "MISKZ", "PRGRP", "PRWRK", "UMREF", "DISLS", "BSTMI", "BSTMA", "BSTFE", "LAGPR", "LOSFX",
    "WAERS", "AUSSS", "MABST", "BSTRF", "TAKZT", "RDPRF", "MEGRU", "EISBE", "EISLO", "SHZET", "LGRAD",
    "RWPRO", "SHFLG", "SHPRO", "AHDIS", "SFTY_STK_METH", "SBDKZ", "KAUSF", "KZBED", "KZAUS", "AUSDT",
    "NFMAT", "SAUFT", "SFEPR", "BESKZ", "SOBSL", "LGPRO", "WZEIT", "KZPSP", "LGFSB", "MRPPP", "DZEIT",
    "PLIFZ", "WEBAZ", "RGEKZ", "VSPVB", "FABKZ", "SCHGT", "FHORI", "SCM_RRP_TYPE", "SCM_HEUR_ID",
    "SCM_RRP_SEL_GROUP", "SCM_PACKAGE_ID", "SCM_LSUOM", "SCM_TARGET_DUR", "SCM_REORD_DUR", "SCM_TSTRID",
    "SCM_GRPRT", "SCM_CONHAP", "SCM_HUNIT", "SCM_GIPRT", "SCM_CONHAP_OUT", "SCM_HUNIT_OUT", "PERKZ",
    "PERIV", "AUFTL", "VRBWK", "VRBMT", "VRBDT", "VRBFK", "AUTRU", "KZKFK", "BASMG", "UEETO", "UNETO",
    "UEETK", "FRTME", "FEVOR", "SFCPF", "RUEZT", "TRANZ", "BEARZ", "ABCIN", "CCFIX", "NCOST", "AWSLS",
    "LOSGR", "MAXLZ", "LZEIH", "VRVEZ", "VBEAZ", "VBAMG", "FPRFM", "BWSCL", "CONS_PROCG",
    "MULTIPLE_EKGRP", "GI_PR_TIME", "SERVG", "PSTATL", "PSTATA", "PSTATE", "PSTATQ", "PSTATV",
]
MARC_BLANK = {"TAXIM", "PRGRP", "PRWRK", "UMREF", "SFTY_STK_METH", "PSTATL", "PSTATA", "PSTATE",
              "PSTATQ", "PSTATV"} | {c for c in MARC_COLUMNS if c.startswith("SCM_")}
MARC_DERIVED = {
    "PRODUCT", "WERKS", "MTVFP", "MMSTA", "MMSTD", "KOKRS", "AUSME",
    "EKGRP", "STRGR", "FXHOR", "VRMOD", "VINT1", "VINT2", "BSTMI", "BSTMA", "BSTFE", "WAERS", "BSTRF",
    "AUSDT", "NFMAT", "BESKZ", "SOBSL", "LGPRO", "LGFSB", "PERKZ", "FRTME", "FEVOR", "SFCPF", "AWSLS",
    "LOSGR",
}
MARC_PASS = [c for c in MARC_COLUMNS if c not in MARC_DERIVED and c not in MARC_BLANK]

MBEW_COLUMNS = [
    "PRODUCT", "BWKEY", "BWTAR", "BWTTY", "MLAST", "BKLAS", "EKLAS", "QKLAS", "VPRSV",
    "WAERS", "VERPR", "STPRS", "PEINH", "ZKPRS", "ZKDAT", "ZPLP1", "ZPLD1", "ZPLP2", "ZPLD2", "ZPLP3",
    "ZPLD3", "BWPRS", "BWPS1", "VJBWS", "BWPEI", "BWPRH", "BWPH1", "VJBWH", "XLIFO", "MYPOL", "ABWKZ",
    "MTUSE", "MTORG", "OWPNR", "HKMAT", "EKALR", "HRKFT", "KOSGR", "BWSPA", "PSTATB", "PSTATG",
]
MBEW_PASS = ["BWTTY", "MLAST", "QKLAS", "VPRSV", "PEINH", "ZKPRS", "ZKDAT", "ZPLP2", "ZPLP3", "BWPRS",
             "BWPS1", "VJBWS", "BWPEI", "BWPRH", "BWPH1", "VJBWH", "XLIFO", "MYPOL", "ABWKZ", "MTUSE",
             "MTORG", "HRKFT", "BWSPA"]

# Technical columns each table must offer to the engine (missing ones are added as NULL).
REQUIRED_COLS = {
    "MARC": ["MATNR", "WERKS", "LVORM", "BESKZ", "SOBSL", "SFCPF", "LGFSB", "LGPRO", "DISMM", "MMSTA",
             "MMSTD", "AUSME", "AUSDT", "NFMAT", "LOSFX", "LOSGR", "AWSLS", "FRTME", "FEVOR", "BSTMI",
             "BSTMA", "BSTFE", "BSTRF"] + MARC_PASS,
    "MARA": ["MATNR", "LVORM", "MTART", "MEINS", "PRDHA"],
    "MARM": ["MATNR", "MEINH", "UMREN"],
    "MBEW": ["MATNR", "BWKEY", "LVORM", "BWTAR", "STPRS", "PEINH", "KOSGR", "ZPLD2", "ZPLD3"] + MBEW_PASS,
    "EKGRP": ["MATNR", "WERKS", "EKGRP"],
}


# --------------------------------------------------------------------------- #
# 2. Small SQL-semantics helpers (NULL never satisfies a condition)
# --------------------------------------------------------------------------- #
def ne(s: pd.Series, v) -> pd.Series:             # SQL  s <> v
    return s.notna() & (s != v)


def notin(s: pd.Series, vals) -> pd.Series:       # SQL  s NOT IN (...)
    return s.notna() & ~s.isin(vals)


def num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def strip_zeros(s: pd.Series) -> pd.Series:       # REGEXP_REPLACE(x, '^0+', '')
    return s.map(lambda v: v.lstrip("0") if isinstance(v, str) else v)


def fmt_num(x):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return np.nan
    t = format(round(float(x), 6), "f")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t or "0"


def case(index, branches, default=np.nan) -> pd.Series:
    """SQL CASE WHEN: first matching branch wins; NULL conditions count as false."""
    res = pd.Series(np.nan, index=index, dtype=object)
    done = pd.Series(False, index=index)
    for cond, val in branches:
        hit = cond.fillna(False).astype(bool) & ~done
        if hit.any():
            res[hit] = val[hit] if isinstance(val, pd.Series) else val
        done |= hit
    rest = ~done
    if rest.any():
        res[rest] = default[rest] if isinstance(default, pd.Series) else default
    return res


_DATE_PATTERNS = [
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T].*)?$"), "ymd"),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), "ymd"),
    (re.compile(r"^(\d{4})/(\d{2})/(\d{2})$"), "ymd"),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), "mdy"),
    (re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$"), "dmy"),
]
ZERO_DATE = re.compile(r"^0+([-./]0+)*$")


def parse_date(v, only=None):
    """Return ISO yyyy-mm-dd or None."""
    if not isinstance(v, str):
        return None
    t = v.strip()
    for rx, order in _DATE_PATTERNS:
        if only and order not in only:
            continue
        m = rx.match(t)
        if m:
            a, b, c = (int(g) for g in m.groups())
            y, mo, d = {"ymd": (a, b, c), "mdy": (c, a, b), "dmy": (c, b, a)}[order]
            try:
                return date(y, mo, d).isoformat()
            except ValueError:
                return None
    return None


def to_date_series(s: pd.Series, mdy: bool = False, strict: bool = False) -> pd.Series:
    """to_date(x) / to_date(x,'MM/dd/yyyy').  Unparseable -> NULL."""
    def conv(v):
        d = parse_date(v, only=("mdy",)) if mdy else parse_date(v)
        if d is None and mdy and not strict:
            d = parse_date(v)
        return d if d else np.nan
    return s.map(conv)


# --------------------------------------------------------------------------- #
# 3. Transformation
# --------------------------------------------------------------------------- #
def derive_base(T, prof: Profile):
    """The `base` CTE (split plants only): (MATNR, _DW) rows with the derived plant.
    'single' (1025) and 'other' (1000/1029) are plain plant mappings and need no base."""
    if prof.mode != "split":
        return None
    # split: UNION ALL of the "X" and "Y" selections, both from MARC join MARA
    X, Y = prof.plant_x, prof.plant_y
    marc, mara = T["MARC"], T["MARA"]
    ok = mara[mara.LVORM.isna() & ne(mara.MTART, "NVAL")][["MATNR"]]
    m = marc[marc.LVORM.isna() & (marc.WERKS == prof.src[0])].merge(ok, on="MATNR", how="inner")
    m = m.reset_index(drop=True)
    b, sob, sf, w = m.BESKZ, m.SOBSL, m.SFCPF, m.WERKS
    F = b == "F"
    # rule book: DAP3 -> X; F & SOBSL null -> X,Y; F -> X; E/X -> X,Y; BESKZ null -> X,Y; else original
    d1 = case(m.index, [(sf == "DAP3", X), (F & sob.isna(), X), (F & sob.notna(), X),
                        (b.isin(["E", "X"]), X), (b.isna(), X)], default=w)
    d2 = case(m.index, [(sf == "DAP3", X), (F & sob.isna(), Y), (F & sob.notna(), X),
                        (b.isin(["E", "X"]), Y), (b.isna(), Y)], default=w)
    out = pd.concat([pd.DataFrame({"MATNR": m.MATNR, "_DW": d1}),
                     pd.DataFrame({"MATNR": m.MATNR, "_DW": d2})])
    return out.drop_duplicates()


def attach_context(d: pd.DataFrame, T, with_ekgrp: bool) -> pd.DataFrame:
    """MARA / MARM_EA / EKGRP joins; MARA.MTART <> 'NVAL' and MARA.LVORM IS NULL filters."""
    d = d.copy()
    d["_K"] = strip_zeros(d.MATNR)
    mara = T["MARA"]
    ma = mara[["MATNR", "LVORM", "MTART", "MEINS", "PRDHA"]].rename(
        columns={"LVORM": "_MARA_LVORM", "MTART": "_MTART", "MEINS": "_MEINS", "PRDHA": "_PRDHA"})
    d = d.merge(ma, on="MATNR", how="left")
    d = d[~(d._MTART == "NVAL")]                               # MARA.MTART <> 'NVAL'
    ea = T["MARM"]
    ea = ea[ea.MEINH == "EA"][["MATNR", "UMREN"]].drop_duplicates("MATNR").rename(columns={"UMREN": "_UMREN"})
    d = d.merge(ea, on="MATNR", how="left")
    if with_ekgrp:
        if T["EKGRP"] is not None:
            e = T["EKGRP"][["MATNR", "WERKS", "EKGRP"]].copy()
            e["_K"] = strip_zeros(e.MATNR)
            e = e[["_K", "WERKS", "EKGRP"]].drop_duplicates().rename(
                columns={"WERKS": "_EW", "EKGRP": "_EKGRP"})
            d = d.merge(e, left_on=["_K", "WERKS"], right_on=["_K", "_EW"], how="left")
        else:
            d["_EKGRP"] = np.nan
    return d[d._MARA_LVORM.isna()].reset_index(drop=True)


def lot_rule(d, col, dw):
    v, um = num(d[col]), num(d["_UMREN"])
    prod = (v * um).map(fmt_num)
    fh = d._MTART.isin(["FERT", "HALB"])
    dap = dw.isin(DAP_PLANTS)
    c1 = fh & dap & ((d.SFCPF == "DAP3") | (d.SOBSL == "30"))
    c2 = fh & dap
    return case(d.index, [(c1, prod), (c2, "0"), (d._MEINS == "CS", prod)], d[col])


def transform_marc(d: pd.DataFrame, prof: Profile) -> pd.DataFrame:
    dw = d["_DW"]
    mt, ix = d._MTART, d.index
    fert = mt == "FERT"
    out = pd.DataFrame(index=ix)
    out["PRODUCT"] = d.MATNR
    out["WERKS"] = dw
    out["MTVFP"] = case(ix, [(mt == "LEIH", "02"), (dw.isin(SP_PLANTS), "SP")], "02")
    mmsta = [(d.MMSTA == "01", "PW"), (d.MMSTA == "BP", "BP"), (d.MMSTA == "ZP", "ZP"),
             (d.MMSTA == "02", "TB"), (d.MMSTA == "BR", "BR")]
    out["MMSTA"] = case(ix, mmsta, d.MMSTA)
    out["MMSTD"] = to_date_series(d.MMSTD)
    out["KOKRS"] = "CG01"
    out["AUSME"] = case(ix, [(dw.isin(DAP_PLANTS), ""), (fert & (d.BESKZ == "E"), "CS")], d.AUSME)
    out["EKGRP"] = case(ix, [(d._EKGRP.notna(), d._EKGRP)], "")
    out["STRGR"] = case(ix, [(fert, "40")], "")
    out["FXHOR"] = case(ix, [(fert, "14"), (d.DISMM == "P1", "14")], "")
    out["VRMOD"] = case(ix, [(fert, "2")], "")
    out["VINT1"] = case(ix, [(fert, "30")], "")
    out["VINT2"] = case(ix, [(fert, "7")], "")
    for c in ("BSTMI", "BSTMA", "BSTFE", "BSTRF"):
        out[c] = lot_rule(d, c, dw)
    out["WAERS"] = case(ix, [(num(d.LOSFX) == 0, "")], "USD")          # NULL LOSFX -> 'USD'
    out["AUSDT"] = to_date_series(d.AUSDT)
    out["NFMAT"] = strip_zeros(d.NFMAT)
    out["BESKZ"] = case(ix, [((d.BESKZ == "E") & dw.isin(SP_PLANTS), "F")], d.BESKZ)

    sob = []
    for plant, m in SOBSL_MAP.items():
        p = dw == plant
        for code, res in m.items():
            if code != "E":
                sob.append((p & (d.BESKZ == "F") & (d.SOBSL == code), res))
        sob.append((p & (d.BESKZ == "E"), m["E"]))
    out["SOBSL"] = case(ix, sob, d.SOBSL)

    # rule book: LGPRO  US30 -> '' ; US27 -> 'PL01' ; else LGPRO
    #            LGFSB  US30 -> 'DWHS' ; US27 -> '' ; else LGPRO (as written in the rule book)
    out["LGPRO"] = case(ix, [(dw == "US30", ""), (dw == "US27", "PL01")], d.LGPRO)
    out["LGFSB"] = case(ix, [(dw == "US30", "DWHS"), (dw == "US27", "")], d.LGPRO)

    out["PERKZ"] = case(ix, [(fert, "W")], "")
    out["FRTME"] = case(ix, [(dw.isin(DAP_PLANTS), ""), (fert & (d.BESKZ == "E"), "CS")], d.FRTME)
    out["FEVOR"] = case(ix, [(dw.isin(DAP_PLANTS), ""), (d.SFCPF == "DAP1", "001"),
                             (d.SFCPF == "DAP2", "002"), (d.SFCPF == "DAP3", "003")], d.FEVOR)
    out["SFCPF"] = case(ix, [(dw.isin(DAP_PLANTS) & ne(d.SFCPF, "DAP3"), ""), (d.SOBSL == "30", "")], d.SFCPF)

    losgr = num(d.LOSGR)
    zero = losgr == 0
    out["AWSLS"] = case(ix, [(zero, ""), (mt.isin(["FERT", "HALB"]), "000001"),
                             (d.AWSLS == "Z00001", "000001")], d.AWSLS)
    out["LOSGR"] = case(ix, [(zero, ""), (fert & (d._MEINS == "CS"), (losgr * num(d._UMREN)).map(fmt_num))],
                        losgr.map(fmt_num))
    out = pd.concat([out, d[MARC_PASS], pd.DataFrame("", index=ix, columns=sorted(MARC_BLANK))], axis=1)
    return out[MARC_COLUMNS].drop_duplicates()


def cs_price(stprs, umren):
    """round(MBEW.STPRS / MARM_EA.UMREN, 2) with HALF_UP rounding (MBEW_Mappings.xlsx)."""
    if pd.isna(stprs) or pd.isna(umren):
        return np.nan
    try:
        s, u = Decimal(str(stprs)), Decimal(str(umren))
        if u == 0:
            return np.nan
        return str((s / u).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return np.nan


def transform_mbew(d: pd.DataFrame, prof: Profile) -> pd.DataFrame:
    dw, mt, ix = d["_DW"], d._MTART, d.index
    out = pd.DataFrame(index=ix)
    out["PRODUCT"] = d.MATNR
    out["BWKEY"] = dw
    out["BWTAR"] = case(ix, [(d.BWTAR == "~", "")], d.BWTAR)
    out["BKLAS"] = case(ix, [(d._PRDHA.isin(["009990099900000999", "00999"]), "RACK")], mt)
    out["EKLAS"] = case(ix, [(mt == "FERT", "YSIT")], np.nan)
    out["WAERS"] = "USD"
    out["VERPR"] = ""
    cs = (mt == "FERT") & (d._MEINS == "CS")
    calc = pd.Series([cs_price(a, c) for a, c in zip(d.STPRS[cs], d._UMREN[cs])],
                     index=d.index[cs], dtype=object)
    out["STPRS"] = case(ix, [(cs, calc.reindex(ix))], d.STPRS)
    planned = case(ix, [(notin(mt, ["FERT", "HALB"]), d.STPRS)], "")   # ZPLD1 = price: as in the SQL
    out["ZPLP1"] = planned
    out["ZPLD1"] = planned
    out["ZPLD2"] = to_date_series(d.ZPLD2, mdy=True, strict=STRICT_DATES)
    out["ZPLD3"] = to_date_series(d.ZPLD3, mdy=True, strict=STRICT_DATES)
    out["OWPNR"] = ""
    out["HKMAT"] = "X"
    out["EKALR"] = "X"
    # KOSGR: DERIVED_WERKS in (CA02, US30, US31, US33) -> '' else KOSGR
    out["KOSGR"] = case(ix, [(dw.isin(["CA02", "US30", "US31", "US33"]), "")], d.KOSGR)
    out["PSTATB"] = ""
    out["PSTATG"] = ""
    out = pd.concat([out, d[MBEW_PASS]], axis=1)
    return out[MBEW_COLUMNS].drop_duplicates()


def build_view(T, prof: Profile, table: str, strict_join: bool = False) -> pd.DataFrame:
    """Reproduce one mm_S_MARC / mm_S_MBEW view for one plant profile.

    ``strict_join`` is kept for API compatibility only: 1000/1029 are now a plain plant
    mapping (no MATNR-only join), so there is no fan-out left to make strict.
    """
    is_marc = table == "MARC"
    src_tbl = T["MARC"] if is_marc else T["MBEW"]
    plant_col = "WERKS" if is_marc else "BWKEY"
    rows = src_tbl[src_tbl[plant_col].isin(prof.src) & src_tbl.LVORM.isna()]
    if prof.mode == "single":                                   # 1025 -> US29
        d = rows.copy()
        d["_DW"] = prof.target
    elif prof.mode == "other":                                  # 1000 -> US26, 1029 -> CA02
        d = rows.copy()
        d["_DW"] = d[plant_col].map(OTHER_PLANT_MAP)
    else:                                                       # 1021 / 1028 / 1030 (BASE CTE)
        base = derive_base(T, prof)
        d = base.merge(rows, on="MATNR", how="inner")          # SQL joins on MATNR only
    if prof.mode != "single":
        d = d[notin(d._DW, list(prof.src))]                    # DERIVED_WERKS not in source plant(s)
    if d.empty:
        return pd.DataFrame(columns=MARC_COLUMNS if is_marc else MBEW_COLUMNS)
    d = attach_context(d, T, with_ekgrp=is_marc)
    if is_marc:
        d["_EKGRP"] = d["_EKGRP"] if "_EKGRP" in d else np.nan
        return transform_marc(d, prof)
    ok = T["MARA"]
    ok = set(ok[ne(ok.MTART, "UNBW") & ok.LVORM.isna()].MATNR)
    d = d[d.MATNR.isin(ok)]
    return transform_mbew(d.reset_index(drop=True), prof)


def transform_table(T, table: str, strict_join: bool = False, only=None) -> pd.DataFrame:
    """Run every plant profile for one table ('MARC' | 'MBEW') and union the results
    (this is `transform_all` of the original script, restricted to one table)."""
    cols = MARC_COLUMNS if table == "MARC" else MBEW_COLUMNS
    parts = []
    for prof in PROFILES:
        if only and prof.label not in only:
            continue
        v = build_view(T, prof, table, strict_join)
        log.info("Expected S4 %s  plant group %-9s -> %8d rows", table, prof.label, len(v))
        parts.append(v)
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)
    key = "WERKS" if table == "MARC" else "BWKEY"
    return df.drop_duplicates().sort_values(["PRODUCT", key]).reset_index(drop=True)