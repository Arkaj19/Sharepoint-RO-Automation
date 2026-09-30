"""
Typed mapping operations - the only way Agent 1 can change a mapping.

The agent emits a list of these; each is validated against the current
mapping and the current data headers, shown to a human with its reason,
confidence and evidence, and only applied after it is accepted.

Gate G1 (mappings): which columns / fields / keys exist and connect.
Gate G2 (rules):    how values are transformed, crosswalked, split, filtered.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from app.mapping import render
from app.mapping.model import (
    CaseStep, DefaultStep, FieldSpec, FilterSpec, IgnoredColumn, LookupSpec, MappingDoc, Provenance, Side,
    TransformStep, ValueMap, ValueMapEntry,
)

OpStatus = Literal["pending", "accepted", "rejected", "applied", "failed", "stale"]

G1_OPS = {"add_field", "remove_field", "update_field_attrs", "set_column_alias", "update_key", "ignore_column",
          "set_dictionary", "set_lookup", "set_source_ref"}


class OpError(ValueError):
    pass


class Decision(BaseModel):
    decision: Literal["accepted", "rejected"]
    by: str | None = None
    at: str | None = None
    comment: str | None = None


class _OpBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    op_id: str = ""
    gate: Literal["G1", "G2"] = "G1"
    confidence: float = Field(default=0.5, ge=0, le=1)
    reason: str = Field(default="", max_length=1500)
    evidence: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    source: Literal["deterministic", "llm", "databricks", "workbook"] = "deterministic"
    # ops sharing a group are alternatives for the same rule: accept at most one
    alternative_group: str | None = None
    status: OpStatus = "pending"
    decision: Decision | None = None
    preview: dict[str, Any] | None = None       # {target, before, after} for the UI
    result: str | None = None                  # apply outcome / validation note

    @model_validator(mode="after")
    def _set_gate(self):
        self.gate = "G1" if getattr(self, "op") in G1_OPS else "G2"
        return self


class FieldAttrs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    s4_label: str | None = None
    group: str | None = None
    mandatory: bool | None = None
    data_type: Literal["Text", "Number", "Date"] | None = None
    length: int | float | None = None
    decimal: int | float | None = None


class AddField(_OpBase):
    op: Literal["add_field"]
    field: FieldSpec


class RemoveField(_OpBase):
    op: Literal["remove_field"]
    field_id: str


class UpdateFieldAttrs(_OpBase):
    op: Literal["update_field_attrs"]
    field_id: str
    set: FieldAttrs


class SetColumnAlias(_OpBase):
    op: Literal["set_column_alias"]
    field_id: str
    side: Side
    column: str | None


class UpdateKey(_OpBase):
    op: Literal["update_key"]
    fields: list[str] = Field(min_length=1)


class IgnoreColumn(_OpBase):
    op: Literal["ignore_column"]
    side: Side
    column: str
    note: str | None = None


class SetTransform(_OpBase):
    op: Literal["set_transform"]
    field_id: str
    transform: list[TransformStep] | None = None     # None = leave unchanged
    compare: list[TransformStep] | None = None
    source_ref: str | None = None                    # "" clears it; None leaves it unchanged
    reference: dict[str, Any] | None = None          # workbook text / SQL the rule came from


class SetDictionary(_OpBase):
    """Technical field -> extract column label for one table."""
    op: Literal["set_dictionary"]
    table: str
    entries: dict[str, str] = Field(min_length=1)


class SetLookup(_OpBase):
    """Add or replace (by id) a lookup into a supporting table."""
    op: Literal["set_lookup"]
    lookup: LookupSpec


class SetSourceRef(_OpBase):
    """Where a field reads from, as a technical reference (MARC.DISMM)."""
    op: Literal["set_source_ref"]
    field_id: str
    source_ref: str | None
    reference: dict[str, Any] | None = None


class AddSplitRule(_OpBase):
    op: Literal["add_split_rule"]
    field_id: str
    case: CaseStep


class SetDefault(_OpBase):
    op: Literal["set_default"]
    field_id: str
    value: str
    when: Literal["blank", "always"] = "blank"


class MapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from")
    to: str


class AddValueMapEntries(_OpBase):
    op: Literal["add_value_map_entries"]
    map_id: str
    entries: list[MapEntry] = Field(min_length=1)
    description: str | None = None
    on_missing: Literal["flag", "passthrough", "default"] | None = None


class RemoveValueMapEntries(_OpBase):
    op: Literal["remove_value_map_entries"]
    map_id: str
    entries: list[MapEntry] = Field(min_length=1)


class AddFilter(_OpBase):
    op: Literal["add_filter"]
    filter: FilterSpec


class RemoveFilter(_OpBase):
    op: Literal["remove_filter"]
    filter_id: str


MappingOp = Annotated[
    Union[AddField, RemoveField, UpdateFieldAttrs, SetColumnAlias, UpdateKey, IgnoreColumn,
          SetTransform, AddSplitRule, SetDefault, AddValueMapEntries, RemoveValueMapEntries,
          AddFilter, RemoveFilter, SetDictionary, SetLookup, SetSourceRef],
    Field(discriminator="op"),
]
_adapter = TypeAdapter(MappingOp)
_list_adapter = TypeAdapter(list[MappingOp])


def parse_op(data: dict) -> Any:
    return _adapter.validate_python(data)


def parse_ops(data: list[dict]) -> list:
    return _list_adapter.validate_python(data)


def dump_op(op) -> dict:
    return op.model_dump(mode="json", by_alias=True, exclude_none=True)


# -- rule ids -------------------------------------------------------------------------

def _next_rule_id(doc: MappingDoc, prefix: str) -> str:
    pat = re.compile(rf"^{re.escape(prefix)}-{re.escape(doc.object)}-(\d+)$")
    nums = []
    provs = [doc.key.provenance] + [f.provenance for f in doc.fields] + [x.provenance for x in doc.filters] \
        + [lk.provenance for lk in doc.lookups] \
        + [e.provenance for vm in doc.value_maps.values() for e in vm.entries] \
        + [c.provenance for c in doc.ignored_columns.ecc + doc.ignored_columns.s4]
    for p in provs:
        m = pat.match(p.rule_id or "")
        if m:
            nums.append(int(m.group(1)))
    return f"{prefix}-{doc.object}-{(max(nums) + 1 if nums else 1):04d}"


def _stamp(doc: MappingDoc, prov: Provenance | None, prefix: str) -> Provenance:
    p = (prov or Provenance(status="approved", source="human")).model_copy()
    p.rule_id = _next_rule_id(doc, prefix)
    return p


def _touch(doc: MappingDoc, existing: Provenance | None, prov: Provenance | None, prefix: str) -> Provenance:
    """Provenance after a change that doesn't touch the rule's logic (column
    name, length, ...): the rule keeps its id, origin, confidence and reason;
    only the approval of this change is recorded (the change log has the details)."""
    if existing is None or existing.rule_id is None:
        return _stamp(doc, prov, prefix)
    new = prov or Provenance(status="approved", source="human")
    p = existing.model_copy()
    for k in ("status", "version", "proposal_id", "approved_by", "approved_at"):
        v = getattr(new, k)
        if v is not None:
            setattr(p, k, v)
    return p


def _require_field(doc: MappingDoc, field_id: str) -> FieldSpec:
    f = doc.field(field_id)
    if f is None:
        raise OpError(f"unknown field '{field_id}'")
    return f


# -- apply ------------------------------------------------------------------------------

def apply_op(doc: MappingDoc, op, prov: Provenance | None = None) -> None:
    """Applies one op to `doc` in place. `prov` is stamped on what changed
    (a fresh rule id is added)."""
    kind = op.op
    if kind == "add_field":
        if doc.field(op.field.id):
            raise OpError(f"field '{op.field.id}' already exists")
        f = op.field.model_copy(deep=True)
        f.provenance = _stamp(doc, prov, "R")
        doc.fields.append(f)
    elif kind == "remove_field":
        _require_field(doc, op.field_id)
        if op.field_id in doc.key.fields:
            raise OpError(f"'{op.field_id}' is part of the key; change the key first")
        doc.fields = [f for f in doc.fields if f.id != op.field_id]
    elif kind == "update_field_attrs":
        f = _require_field(doc, op.field_id)
        for k, v in op.set.model_dump(exclude_unset=True).items():
            setattr(f, k, v)
        f.provenance = _touch(doc, f.provenance, prov, "R")
    elif kind == "set_column_alias":
        f = _require_field(doc, op.field_id)
        setattr(f, "ecc_column" if op.side == "ecc" else "s4_column", op.column)
        f.provenance = _touch(doc, f.provenance, prov, "R")
    elif kind == "update_key":
        for k in op.fields:
            _require_field(doc, k)
        doc.key.fields = list(op.fields)
        doc.key.provenance = _stamp(doc, prov, "K")
    elif kind == "ignore_column":
        lst = getattr(doc.ignored_columns, op.side)
        if any(c.column == op.column for c in lst):
            raise OpError(f"{op.side.upper()} column '{op.column}' is already ignored")
        lst.append(IgnoredColumn(column=op.column, reason=op.note or op.reason[:200],
                                 provenance=_stamp(doc, prov, "I")))
    elif kind == "set_transform":
        f = _require_field(doc, op.field_id)
        if op.transform is None and op.compare is None:
            raise OpError("set_transform needs 'transform' and/or 'compare'")
        logic_before = f.model_dump(mode="json", include={"transform", "compare", "source_ref"})
        if op.transform is not None:
            f.transform = [s.model_copy(deep=True) for s in op.transform]
        if op.compare is not None:
            f.compare = [s.model_copy(deep=True) for s in op.compare]
        if op.source_ref is not None:
            f.source_ref = op.source_ref or None
        if op.reference is not None:
            f.reference = dict(op.reference)
        same_logic = f.model_dump(mode="json", include={"transform", "compare", "source_ref"}) == logic_before
        # confirming an unchanged rule (e.g. after a workbook conflict) keeps its identity; a new rule gets a new id
        f.provenance = _touch(doc, f.provenance, prov, "R") if same_logic else _stamp(doc, prov, "R")
    elif kind == "add_split_rule":
        f = _require_field(doc, op.field_id)
        case = op.case.model_copy(deep=True)
        if f.transform and isinstance(f.transform[0], CaseStep):
            existing = f.transform[0]
            existing.cases = case.cases + existing.cases
        else:
            case.otherwise = case.otherwise or list(f.transform)
            f.transform = [case]
        f.provenance = _stamp(doc, prov, "R")
    elif kind == "set_default":
        f = _require_field(doc, op.field_id)
        f.transform = [s for s in f.transform if not isinstance(s, DefaultStep)] + \
            [DefaultStep(op="default", value=op.value, when=op.when)]
        f.provenance = _stamp(doc, prov, "R")
    elif kind == "add_value_map_entries":
        vm = doc.value_maps.get(op.map_id)
        if vm is None:
            vm = ValueMap(description=op.description, on_missing=op.on_missing or "flag")
            doc.value_maps[op.map_id] = vm
        elif op.on_missing:
            vm.on_missing = op.on_missing
        existing = {(e.from_, e.to) for e in vm.entries}
        for e in op.entries:
            if (e.from_, e.to) in existing:
                continue
            vm.entries.append(ValueMapEntry(**{"from": e.from_, "to": e.to}, provenance=_stamp(doc, prov, "VM")))
    elif kind == "remove_value_map_entries":
        vm = doc.value_maps.get(op.map_id)
        if vm is None:
            raise OpError(f"unknown value map '{op.map_id}'")
        drop = {(e.from_, e.to) for e in op.entries}
        vm.entries = [e for e in vm.entries if (e.from_, e.to) not in drop]
    elif kind == "set_dictionary":
        table = op.table.upper()
        doc.dictionary.setdefault(table, {})
        doc.dictionary[table].update({k.upper(): v for k, v in op.entries.items()})
    elif kind == "set_lookup":
        lk = op.lookup.model_copy(deep=True)
        lk.provenance = _stamp(doc, prov, "L")
        idx = next((i for i, x in enumerate(doc.lookups) if x.id.upper() == lk.id.upper()), None)
        if idx is None:
            doc.lookups.append(lk)
        else:
            doc.lookups[idx] = lk            # replace in place: keeps the YAML order (clean diffs)
    elif kind == "set_source_ref":
        f = _require_field(doc, op.field_id)
        f.source_ref = op.source_ref
        if op.reference is not None:
            f.reference = dict(op.reference)
        f.provenance = _stamp(doc, prov, "R")
    elif kind == "add_filter":
        if any(x.id == op.filter.id for x in doc.filters):
            raise OpError(f"filter '{op.filter.id}' already exists")
        flt = op.filter.model_copy(deep=True)
        flt.provenance = _stamp(doc, prov, "F")
        doc.filters.append(flt)
    elif kind == "remove_filter":
        if not any(x.id == op.filter_id for x in doc.filters):
            raise OpError(f"unknown filter '{op.filter_id}'")
        doc.filters = [x for x in doc.filters if x.id != op.filter_id]
    else:  # pragma: no cover
        raise OpError(f"unsupported op '{kind}'")


def revalidate(doc: MappingDoc) -> MappingDoc:
    """Full model validation (cross references) of a mutated document."""
    try:
        return MappingDoc.model_validate(doc.model_dump(mode="json", by_alias=True))
    except ValidationError as exc:
        raise OpError("; ".join(e["msg"] for e in exc.errors())) from exc


# -- semantic validation --------------------------------------------------------------

def _check_columns(op, headers: dict[str, set[str]], doc: MappingDoc) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if op.op == "set_column_alias" and op.column is not None:
        cols = headers.get(op.side, set())
        if cols and op.column not in cols:
            errors.append(f"{op.side.upper()} column '{op.column}' does not exist in the current data")
        attr = "ecc_column" if op.side == "ecc" else "s4_column"
        other = [f.id for f in doc.fields if getattr(f, attr) == op.column and f.id != op.field_id]
        if other:
            warnings.append(f"column '{op.column}' is already used by field(s) {other}")
    if op.op == "ignore_column":
        cols = headers.get(op.side, set())
        if cols and op.column not in cols:
            errors.append(f"{op.side.upper()} column '{op.column}' does not exist in the current data")
    if op.op == "add_field":
        for side, attr in (("ecc", "ecc_column"), ("s4", "s4_column")):
            col = getattr(op.field, attr)
            if col and headers.get(side) and col not in headers[side]:
                errors.append(f"{side.upper()} column '{col}' does not exist in the current data")
    return errors, warnings


def preview(doc: MappingDoc, op) -> dict:
    """{target, before, after} text for one op against `doc` (before apply)."""
    kind = op.op
    if kind in ("set_column_alias", "update_field_attrs", "set_transform", "add_split_rule", "set_default",
                "remove_field", "set_source_ref"):
        f = doc.field(op.field_id)
        target = f"field {op.field_id}" + (f" ({f.s4_label})" if f else "")
        before = render.field_summary(f, doc) if f else None
        after = None
        if f and kind != "remove_field":
            tmp = doc.model_copy(deep=True)
            try:
                apply_op(tmp, op)
                after = render.field_summary(tmp.field(op.field_id), tmp)
            except OpError as exc:
                after = {"error": str(exc)}
        return {"target": target, "before": before, "after": after}
    if kind == "set_dictionary":
        before = doc.dictionary.get(op.table.upper(), {})
        changed = {k: v for k, v in op.entries.items() if before.get(k.upper()) != v}
        return {"target": f"dictionary {op.table.upper()}",
                "before": {k: before.get(k.upper()) for k in changed} or None,
                "after": changed or "no change"}
    if kind == "set_lookup":
        old = doc.lookup(op.lookup.id)
        return {"target": f"lookup {op.lookup.id}", "before": render.lookup_text(old) if old else None,
                "after": render.lookup_text(op.lookup)}
    if kind == "add_field":
        return {"target": f"new field {op.field.id}", "before": None, "after": render.field_summary(op.field, doc)}
    if kind == "update_key":
        return {"target": "business key", "before": doc.key.fields, "after": op.fields}
    if kind == "ignore_column":
        return {"target": f"{op.side.upper()} column {op.column}", "before": "unmapped", "after": "ignored"}
    if kind in ("add_value_map_entries", "remove_value_map_entries"):
        vm = doc.value_maps.get(op.map_id)
        return {"target": f"value map {op.map_id}",
                "before": [f"{e.from_} -> {e.to}" for e in vm.entries] if vm else None,
                "after": [f"{'+' if kind.startswith('add') else '-'} {e.from_} -> {e.to}" for e in op.entries]}
    if kind == "add_filter":
        return {"target": f"filter {op.filter.id}", "before": None, "after": render.filter_text(op.filter)}
    if kind == "remove_filter":
        flt = next((x for x in doc.filters if x.id == op.filter_id), None)
        return {"target": f"filter {op.filter_id}", "before": render.filter_text(flt) if flt else None,
                "after": None}
    return {"target": kind, "before": None, "after": None}


def validate_ops(doc: MappingDoc, ops: list, headers: dict[str, set[str]] | None = None) -> list[dict]:
    """Checks each op in order on a working copy (so later ops may rely on
    earlier ones). Returns one {index, op_id, ok, errors, warnings} per op;
    ops that fail are not applied to the working copy."""
    headers = headers or {}
    work = doc.model_copy(deep=True)
    ids = {op.op_id for op in ops if op.op_id}
    results = []
    for i, op in enumerate(ops):
        errors, warnings = _check_columns(op, headers, work)
        missing_deps = [d for d in op.depends_on if d not in ids]
        if missing_deps:
            errors.append(f"depends_on refers to unknown op(s) {missing_deps}")
        if not op.reason.strip():
            warnings.append("no reason given")
        if not errors:
            trial = work.model_copy(deep=True)
            try:
                apply_op(trial, op)
                trial = revalidate(trial)
            except OpError as exc:
                errors.append(str(exc))
            else:
                op.preview = preview(work, op)
                work = trial
        results.append({"index": i, "op_id": op.op_id, "op": op.op, "ok": not errors,
                        "errors": errors, "warnings": warnings})
    return results
