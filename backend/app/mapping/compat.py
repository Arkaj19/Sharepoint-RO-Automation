"""
Transitional adapter: exposes the YAML mapping through the old
mapping_loader API (FieldMapping, get_field_mappings, get_key_fields,
get_mandatory_fields). Used to prove the YAML migration is lossless; new
code should use app.mapping.repository / MappingDoc directly.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.mapping import repository


@dataclass(frozen=True)
class FieldMapping:
    s4_field: str
    group: str | None
    mandatory: bool
    is_key_marker: bool
    data_type: str | None
    length: float | None
    decimal: float | None
    sheet: str
    source_table: str | None
    source_field: str | None
    source_field_note: str | None


def get_field_mappings(sheet: str) -> list[FieldMapping]:
    doc = repository.load(sheet)
    return [
        FieldMapping(
            s4_field=f.s4_label,
            group=f.group,
            mandatory=f.mandatory,
            is_key_marker=bool(f.group and f.group.strip().lower() == "key"),
            data_type=f.data_type,
            length=f.length,
            decimal=f.decimal,
            sheet=doc.s4_sheet or sheet,
            source_table=f.ecc_source.table,
            source_field=f.ecc_source.field,
            source_field_note=f.ecc_source.note,
        )
        for f in doc.fields
    ]


def get_key_fields(sheet: str) -> list[str]:
    return [f.s4_label for f in repository.load(sheet).key_fields()]


def get_mandatory_fields(sheet: str) -> list[str]:
    return [f.s4_label for f in repository.load(sheet).fields if f.mandatory]


def get_condition_notes(sheet: str) -> str | None:
    return repository.load(sheet).raw_condition_notes
