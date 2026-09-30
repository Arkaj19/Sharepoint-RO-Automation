"""
One-time migration: legacy mapping (marc_mbew_mapping.json, parsed from
MARC_MBEW_Mappings.xlsx) -> mappings/<OBJECT>.yaml.

  python -m scripts.migrate_mapping_to_yaml [--json PATH] [--force]

* Field ids are slugs of the S/4 label (duplicates get the ECC field name
  appended, e.g. price_unit_peinh).
* s4_column is the real S/4 header whose name matches the workbook's
  technical field (case-insensitive), read from the legacy combined S4 CSV.
* ecc_column is left empty - Agent 1's bootstrap run proposes it.
* The business key is the workbook's "Key" field plus Plant (MARC) /
  Valuation Area (MBEW) - the rule mapping_loader used to hard-code.

Before writing, the script checks that the old mapping_loader view of every
field is reproduced exactly from the new documents.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core import paths  # noqa: E402
from app.mapping import repository  # noqa: E402
from app.mapping.model import EccSource, FieldSpec, KeySpec, MappingDoc, Provenance  # noqa: E402

LEGACY_JSON = BACKEND_DIR / "tests" / "fixtures" / "legacy_marc_mbew_mapping.json"

OBJECT_META = {
    "MARC": {"title": "Plant Data", "secondary_key": "Plant"},
    "MBEW": {"title": "Valuation Data", "secondary_key": "Valuation Area"},
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "field"


def _s4_headers(obj: str) -> list[str]:
    p = paths.legacy_combined_dir() / obj / "S4" / f"{obj}_S4_combined.csv"
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh))


def build_doc(obj: str, raw: dict) -> MappingDoc:
    fields_raw = raw["fields"]
    headers = {h.strip().upper(): h for h in _s4_headers(obj)}
    label_counts = Counter(_slug(f["s4_field"]) for f in fields_raw)

    fields: list[FieldSpec] = []
    for f in fields_raw:
        fid = _slug(f["s4_field"])
        if label_counts[fid] > 1:
            fid = f"{fid}_{_slug(f['source_field'] or 'x')}"
        s4_col = headers.get((f["source_field"] or "").strip().upper())
        fields.append(
            FieldSpec(
                id=fid,
                s4_label=f["s4_field"],
                s4_column=s4_col,
                ecc_column=None,
                ecc_source=EccSource(table=f["source_table"], field=f["source_field"],
                                     note=f["source_field_note"]),
                group=f["group"],
                mandatory=f["mandatory"],
                data_type=f["data_type"],
                length=f["length"],
                decimal=f["decimal"],
                provenance=Provenance(
                    status="migrated", source="migration",
                    confidence=1.0 if s4_col else None,
                    reason=("S/4 column matched by technical name from the workbook"
                            if s4_col else "No S/4 column matched the workbook's technical name"),
                ),
            )
        )

    sheets = {f["sheet"] for f in fields_raw}
    if len(sheets) != 1:
        raise SystemExit(f"{obj}: expected one S/4 sheet per object, found {sheets}")

    primary = next(fs.id for fs, fr in zip(fields, fields_raw) if fr["is_key_marker"])
    secondary_label = OBJECT_META.get(obj, {}).get("secondary_key")
    secondary = next((fs.id for fs in fields if fs.s4_label == secondary_label), None)
    key_ids = [k for k in (primary, secondary) if k]

    return MappingDoc(
        object=obj,
        title=OBJECT_META.get(obj, {}).get("title"),
        s4_sheet=sheets.pop(),
        mapping_version=1,
        key=KeySpec(fields=key_ids, provenance=Provenance(
            status="migrated", source="migration", confidence=1.0,
            reason="Workbook 'Key' field plus the plant / valuation-area level key")),
        raw_condition_notes=raw.get("condition_notes"),
        fields=fields,
    )


def _legacy_view(raw_fields: list[dict]) -> list[tuple]:
    keys = ["s4_field", "group", "mandatory", "is_key_marker", "data_type", "length", "decimal",
            "sheet", "source_table", "source_field", "source_field_note"]
    return [tuple(f[k] for k in keys) for f in raw_fields]


def _compat_view(doc: MappingDoc) -> list[tuple]:
    return [
        (f.s4_label, f.group, f.mandatory, bool(f.group and f.group.strip().lower() == "key"),
         f.data_type, f.length, f.decimal, doc.s4_sheet, f.ecc_source.table, f.ecc_source.field,
         f.ecc_source.note)
        for f in doc.fields
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=LEGACY_JSON)
    ap.add_argument("--force", action="store_true", help="overwrite existing YAML files")
    args = ap.parse_args()

    raw_all = json.loads(args.json.read_text(encoding="utf-8"))
    for obj, raw in raw_all.items():
        doc = build_doc(obj, raw)
        if _compat_view(doc) != _legacy_view(raw["fields"]):
            raise SystemExit(f"{obj}: migrated mapping does not reproduce the legacy fields")
        out = repository.mapping_path(obj)
        if out.exists() and not args.force:
            print(f"skip {obj}: {out} exists (use --force)")
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()
        saved = repository.save(doc, updated_by="migration", bump=False)
        matched = sum(1 for f in saved.fields if f.s4_column)
        print(f"wrote {out}: {len(saved.fields)} fields, key={saved.key.fields}, "
              f"s4_column matched {matched}/{len(saved.fields)}")


if __name__ == "__main__":
    main()
