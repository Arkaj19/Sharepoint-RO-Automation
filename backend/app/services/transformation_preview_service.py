"""
Transformation Preview Service (MARC)

Runs the real ECC -> S/4 engine on a handful of ECC rows and returns, for every
S/4 row produced, the fields that changed (ECC value | rule | S/4 value) in the
shape the <TransformationPreview/> React component reads.

    GET /api/preview/transformation/MARC?matnr=7079800107&matnr=7079800120
    GET /api/preview/transformation/MARC?limit=20

No stored S/4 data is used: the S/4 side is the engine's own output.
"""
from __future__ import annotations

import logging
import os
import uuid
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from app.core.config import settings
from app.services import ecc_to_s4 as engine
from app.services import ecc_transformation_service as svc
from app.services.table_io import find_latest, read_table

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# S/4 plant -> ECC source plant, derived from the engine's own profiles
# --------------------------------------------------------------------------- #
def _s4_to_ecc_plants() -> dict[str, str]:
    m: dict[str, str] = {}
    for p in engine.PROFILES:
        if p.mode == "split":
            m[p.plant_x] = p.src[0]
            m[p.plant_y] = p.src[0]
        elif p.mode == "single":
            m[p.target] = p.src[0]
    for src, tgt in engine.OTHER_PLANT_MAP.items():
        m[tgt] = src
    return m


S4_TO_ECC = _s4_to_ecc_plants()

# field -> (short label used in the "rules applied" card, detail shown in the row)
RULES: dict[str, tuple[str, str]] = {
    "WERKS": ("Plant mapping", "1021→US30/US27, 1028→US31/US28, 1030→US33/US32, 1025→US29, 1000→US26, 1029→CA02"),
    "BESKZ": ("Procurement type", "E→F at SP plants (US30/US31/US33)"),
    "SOBSL": ("Special procurement", "US30: 40→64, 41→67, 42→6A; BESKZ E→62"),
    "SFCPF": ("Prod. sched. profile", "Blanked at DAP plants unless DAP3; blank if SOBSL = 30"),
    "LGPRO": ("Storage location", "US30 → blank, US27 → PL01, else ECC"),
    "LGFSB": ("Storage location", "US30 → DWHS, US27 → blank, else LGPRO (as written in rule book)"),
    "LOSGR": ("Lot size conversion", "× MARM EA denominator when FERT and base unit CS; 0 → blank"),
    "BSTMI": ("Lot size conversion", "× MARM EA denominator (FERT/HALB at DAP plants or CS)"),
    "BSTMA": ("Lot size conversion", "× MARM EA denominator (FERT/HALB at DAP plants or CS)"),
    "BSTFE": ("Lot size conversion", "× MARM EA denominator (FERT/HALB at DAP plants or CS)"),
    "BSTRF": ("Lot size conversion", "× MARM EA denominator (FERT/HALB at DAP plants or CS)"),
    "AWSLS": ("Variance key", "FERT/HALB → 000001; Z00001 → 000001; LOSGR 0 → blank"),
    "MMSTA": ("Plant material status", "01→PW, 02→TB, BP/ZP/BR unchanged"),
    "MMSTD": ("Date format", "Converted to ISO yyyy-mm-dd"),
    "AUSDT": ("Date format", "Converted to ISO yyyy-mm-dd"),
    "AUSME": ("Unit of issue", "Blank at DAP plants; FERT + BESKZ E → CS"),
    "FRTME": ("Production unit", "Blank at DAP plants; FERT + BESKZ E → CS"),
    "FEVOR": ("Prod. supervisor", "Blank at DAP plants; DAP1/2/3 → 001/002/003"),
    "MTVFP": ("Constant / derived", "SP at US30/US31/US33, else 02"),
    "KOKRS": ("Constant / derived", "Constant CG01"),
    "STRGR": ("Constant / derived", "FERT → 40"),
    "FXHOR": ("Constant / derived", "FERT or MRP type P1 → 14"),
    "VRMOD": ("Constant / derived", "FERT → 2"),
    "VINT1": ("Constant / derived", "FERT → 30"),
    "VINT2": ("Constant / derived", "FERT → 7"),
    "PERKZ": ("Constant / derived", "FERT → W"),
    "WAERS": ("Constant / derived", "USD unless ordering costs = 0"),
    "EKGRP": ("Constant / derived", "From EKGRP lookup, else blank"),
    "NFMAT": ("Leading zeros", "Leading zeros stripped"),
}
DEFAULT_RULE = ("Other", "Derived / blanked by rule book")

# Columns shown as "ECC | S/4" pairs in the Excel-style table (same as the validation workbook)
PAIR_FIELDS = ["BESKZ", "SOBSL", "SFCPF", "LGPRO", "LGFSB", "MMSTA", "MMSTD"]

# Differences already seen against the validated S/4 reference workbook (material 7079800107).
# Remove an entry once the rule book / engine is fixed.
# Keyed by (MATNR, S/4 plant, field): only what was actually verified is flagged.
KNOWN_GAPS: dict[tuple[str, str, str], str] = {
    ("7079800107", "US31", "SOBSL"): "Reference S/4 shows 64 (40→64); engine only maps SOBSL at US30.",
    ("7079800107", "US31", "LGPRO"): "Reference S/4 shows blank; engine only blanks LGPRO at US30.",
    ("7079800107", "US29", "LGFSB"): "Reference S/4 shows blank; engine copies LGPRO (rule book 'else LGPRO').",
}


def _norm(v):
    """None for NULL/blank, otherwise the string value."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _same(a, b) -> bool:
    if a == b:
        return True
    try:
        return a is not None and b is not None and float(a) == float(b)
    except ValueError:
        return False


class TransformationPreviewService:
    def _load_ecc(self, table: str) -> pd.DataFrame:
        d = os.path.join(settings.OUTPUT_DIR, table, "ECC")
        return read_table(find_latest(d, f"{table}_ECC_combined"))

    def get_transformation_preview(self, table_type: str, matnrs: list[str] | None = None,
                                   limit: int = 20) -> dict:
        if table_type != "MARC":
            raise ValueError("Only MARC is supported in this preview")

        raw = self._load_ecc("MARC")
        total_available = len(raw)
        requested = [m.strip().lstrip("0") for m in (matnrs or []) if m.strip()]

        mat_col = next((c for c in raw.columns if str(c).strip().upper() in ("MATERIAL", "MATNR")), None)
        if mat_col is None:
            raise ValueError("MARC ECC file has no Material/MATNR column")

        if matnrs:
            want = {m.strip().lstrip("0") for m in matnrs if m.strip()}
            keys = (raw[mat_col].astype(str).str.strip()
                    .str.replace(r"\.0+$", "", regex=True).str.lstrip("0"))
            keep = keys.isin(want)
            raw = raw[keep]
        else:
            raw = raw.head(limit)
        raw = raw.reset_index(drop=True)

        ecc = svc.normalize_ecc("MARC", raw)                # technical field names
        expected = svc.build_expected_s4("MARC", raw)       # the real engine run

        refs = svc.load_reference_tables()
        mtart = dict(zip(refs["MARA"].MATNR, refs["MARA"].MTART))
        meins = dict(zip(refs["MARA"].MATNR, refs["MARA"].MEINS))
        ea = refs["MARM"][refs["MARM"].MEINH == "EA"].drop_duplicates("MATNR")
        umren = dict(zip(ea.MATNR, ea.UMREN))

        records, rows, rule_counts = [], [], Counter()
        n_pass = n_warn = 0
        produced: set[tuple[str, str]] = set()

        for _, s in expected.iterrows():
            src_plant = S4_TO_ECC.get(s["WERKS"])
            hit = ecc[(ecc["MATNR"] == s["PRODUCT"]) & (ecc["WERKS"] == src_plant)]
            if hit.empty:
                continue
            e = hit.iloc[0]
            produced.add((e["MATNR"], e["WERKS"]))

            fields = []
            for col in engine.MARC_COLUMNS:
                if col == "PRODUCT":
                    continue
                ev = _norm(e["WERKS"] if col == "WERKS" else (e[col] if col in e.index else None))
                sv = _norm(s[col])
                if _same(ev, sv):
                    continue
                label, detail = RULES.get(col, DEFAULT_RULE)
                gap = KNOWN_GAPS.get((s["PRODUCT"], s["WERKS"], col))
                status = "warning" if gap else "pass"
                n_warn += bool(gap)
                n_pass += not gap
                rule_counts[label] += 1
                fields.append({
                    "source_field": col, "source_value": ev, "rule_applied": detail,
                    "target_value": sv, "validation_status": status, "notes": gap or "",
                })

            # known gaps where the engine left the value unchanged are not in `fields` yet: add them
            shown = {f["source_field"] for f in fields}
            for (gm, gw, gcol), note in KNOWN_GAPS.items():
                if gm == s["PRODUCT"] and gw == s["WERKS"] and gcol not in shown:
                    label, detail = RULES.get(gcol, DEFAULT_RULE)
                    n_warn += 1
                    rule_counts[label] += 1
                    fields.append({"source_field": gcol, "source_value": _norm(e[gcol]), "rule_applied": detail,
                                   "target_value": _norm(s[gcol]), "validation_status": "warning", "notes": note})

            m = s["PRODUCT"]
            pairs = {}
            for col in PAIR_FIELDS:
                ev, sv = _norm(e[col]), _norm(s[col])
                pairs[col] = {"ecc": ev, "s4": sv, "changed": not _same(ev, sv),
                              "note": KNOWN_GAPS.get((m, s["WERKS"], col))}
            try:
                conv = float(umren[m]) if (mtart.get(m) == "FERT" and meins.get(m) == "CS") else 1.0
            except (KeyError, TypeError, ValueError):
                conv = 1.0
            summary = [f"Plant {e['WERKS']}→{s['WERKS']}"] + [
                f"{f['source_field']} {f['source_value'] or 'blank'}→{f['target_value'] or 'blank'}"
                for f in fields
                if f["source_field"] in PAIR_FIELDS + ["LOSGR"] and f["source_value"] != f["target_value"]]
            rows.append({
                "record_id": f"{m}-{s['WERKS']}", "matnr": m, "type": mtart.get(m),
                "ecc_plant": e["WERKS"], "s4_plant": s["WERKS"], "pairs": pairs,
                "conversion": conv, "ecc_losgr": _norm(e["LOSGR"]), "s4_losgr": _norm(s["LOSGR"]),
                "rules": "; ".join(summary),
                "status": "warning" if any(p["note"] for p in pairs.values()) else "success",
            })

            records.append({
                "record_id": f"{s['PRODUCT']}-{s['WERKS']}",
                "overall_status": "warning" if any(f["validation_status"] != "pass" for f in fields) else "success",
                "key_fields": {"MATNR": s["PRODUCT"], "ECC Plant": e["WERKS"], "S/4 Plant": s["WERKS"]},
                "field_transformations": fields,
            })

        mara_lvorm = dict(zip(refs["MARA"].MATNR, refs["MARA"].LVORM))
        ruled_plants = set(S4_TO_ECC.values())

        def _why(r) -> str:
            if r["WERKS"] not in ruled_plants:
                return f"Plant {r['WERKS']} has no transformation rule"
            if r["MATNR"] not in mtart:
                return "Material not found in MARA extract"
            if mtart[r["MATNR"]] == "NVAL":
                return "Material type NVAL is excluded"
            if _norm(mara_lvorm.get(r["MATNR"])):
                return "Material flagged for deletion in MARA"
            if _norm(r["LVORM"]):
                return "Plant-level deletion flag set"
            return "Filtered out by engine rules"

        unmapped = [
            {"MATNR": r["MATNR"], "ECC Plant": r["WERKS"], "reason": _why(r)}
            for _, r in ecc.iterrows() if (r["MATNR"], r["WERKS"]) not in produced
        ]
        found = set(ecc["MATNR"])
        total = n_pass + n_warn
        return {
            "preview_id": uuid.uuid4().hex[:12],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "table_type": "MARC",
            "preview_record_count": len(records),
            "total_records_available": total_available,
            "ecc_rows_in": len(ecc),
            "matnrs_requested": requested,
            "matnrs_not_in_ecc": [m for m in requested if m not in found],
            "unmapped_ecc_rows": unmapped,           # e.g. plants with no profile (1055) or NVAL materials
            "summary": {
                "success_rate": f"{(100 * n_pass / total):.1f}%" if total else "n/a",
                "success_count": n_pass,
                "total_transformations": total,
                "total_fields_per_record": len(engine.MARC_COLUMNS) - 1,
                "transformation_rules_applied": dict(rule_counts.most_common()),
            },
            "schema": {
                "key_fields": ["MATNR", "WERKS (ECC plant → S/4 plant)"],
                "common_transformation_types": [
                    "Plant split (one ECC row → up to two S/4 rows)",
                    "Value mapping (BESKZ, SOBSL, MMSTA)",
                    "Unit conversion via MARM (LOSGR, BSTMI …)",
                    "Blanking at DAP plants",
                    "Constants (KOKRS, STRGR, FXHOR …)",
                    "Date reformatting",
                ],
            },
            "pair_fields": PAIR_FIELDS,
            "rows": rows,                             # Excel-style table (one row per S/4 record)
            "records": records,                       # field-level detail (changed fields only)
        }


transformation_service = TransformationPreviewService()