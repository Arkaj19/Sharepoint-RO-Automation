"""
Typed model of one YAML mapping file (mappings/<OBJECT>.yaml).

The YAML is the single source of truth for what "correctly converted" means
for one migration object (MARC, MBEW, ...):

  * which ECC column feeds which S/4 column - both named explicitly, because
    the ECC extract uses SAP screen labels and the S/4 extract uses technical
    field names;
  * how an ECC value becomes the expected S/4 value (`transform`), and how
    both sides are normalised before comparing (`compare`);
  * the business key, value maps (crosswalks), split rules and filters;
  * where every element came from (`provenance`): migrated from the old
    workbook, or approved from an Agent 1 proposal.

Only deterministic code interprets this model (see app/transforms/engine.py).
The agent never edits the YAML directly - it proposes typed operations
(app/mapping/ops.py) that a human approves.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Side = Literal["ecc", "s4"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Provenance(_Model):
    # "migrated" = imported from the old workbook, not yet reviewed here.
    # "approved" = applied from an approved proposal (or confirmed by a human).
    status: Literal["migrated", "approved"] = "migrated"
    confidence: float | None = None
    reason: str | None = None
    rule_id: str | None = None
    version: int | None = None           # mapping_version that last changed it
    source: Literal["migration", "agent", "human", "databricks", "workbook"] = "migration"
    proposal_id: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None


# -- conditions (used by split rules and filters) ----------------------------

class Condition(_Model):
    """Either a leaf test, or an all/any group of conditions.

    A leaf tests one of:
      * `field` - a field id. By default (`on: source`) the source value of
        that field (ECC column for ECC-side logic, S/4 column for S/4-side
        filters); `on: target` tests the value routing / a fan-out value map
        produced for it, e.g. "the S/4 plant this row goes to is US30";
      * `ref` - a technical SAP reference `ALIAS.FIELD`, e.g. `MARC.BESKZ`
        (the object's own ECC table) or `MARA.MTART` (a lookup), resolved to
        extract columns through the mapping's dictionary.

    `sql_null` gives SQL semantics to blanks (a blank value never satisfies
    ne / not_in, as NULL wouldn't in SQL). `numeric` compares as numbers
    ('0.000' equals '0').
    """
    field: str | None = None
    ref: str | None = None
    on: Literal["source", "target"] = "source"
    op: Literal["eq", "ne", "in", "not_in", "is_null", "not_null"] | None = None
    value: str | None = None
    values: list[str] | None = None
    numeric: bool = False
    sql_null: bool = False
    all: list["Condition"] | None = None
    any: list["Condition"] | None = None

    @model_validator(mode="after")
    def _shape(self) -> "Condition":
        is_group = self.all is not None or self.any is not None
        is_leaf = self.field is not None or self.ref is not None or self.op is not None
        if is_group == is_leaf:
            raise ValueError("a condition is either a leaf (field/ref + op) or a group (all / any)")
        if is_leaf:
            if bool(self.field) == bool(self.ref) or not self.op:
                raise ValueError("a leaf condition needs 'op' and exactly one of 'field' / 'ref'")
            if self.ref and "." not in self.ref:
                raise ValueError(f"ref '{self.ref}' must look like ALIAS.FIELD")
            if self.op in ("eq", "ne") and self.value is None:
                raise ValueError(f"op '{self.op}' needs 'value'")
            if self.op in ("in", "not_in") and not self.values:
                raise ValueError(f"op '{self.op}' needs 'values'")
        return self

    def field_refs(self) -> set[str]:
        if self.field:
            return {self.field}
        refs: set[str] = set()
        for c in (self.all or []) + (self.any or []):
            refs |= c.field_refs()
        return refs

    def sap_refs(self) -> set[str]:
        if self.ref:
            return {self.ref}
        refs: set[str] = set()
        for c in (self.all or []) + (self.any or []):
            refs |= c.sap_refs()
        return refs


# -- transform steps ------------------------------------------------------------

class StripStep(_Model):
    op: Literal["strip"]


class UpperStep(_Model):
    op: Literal["upper"]


class LowerStep(_Model):
    op: Literal["lower"]


class StripLeadingZerosStep(_Model):
    op: Literal["strip_leading_zeros"]


class ZfillStep(_Model):
    op: Literal["zfill"]
    width: int = Field(ge=1, le=64)


class ValueMapStep(_Model):
    op: Literal["value_map"]
    map: str                               # key in MappingDoc.value_maps


class DefaultStep(_Model):
    op: Literal["default"]
    value: str
    when: Literal["blank", "always"] = "blank"


class ConstantStep(_Model):
    op: Literal["constant"]
    value: str


class NumberStep(_Model):
    """Canonical number text: '1.0' -> '1', '0012.50' -> '12.5'.
    With `decimals`, format to exactly that many decimal places."""
    op: Literal["number"]
    decimals: int | None = Field(default=None, ge=0, le=12)


class DateStep(_Model):
    op: Literal["date"]
    formats: list[str] = Field(default_factory=lambda: [
        "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%m/%d/%Y", "%Y%m%d"])
    to_format: str = "%Y-%m-%d"


class FromRefStep(_Model):
    """Take the value of another column: `MARC.LGPRO`, `MARA.MTART`, or
    `TARGET.<field id>` (the routed / fanned-out value of a key field)."""
    op: Literal["from_ref"]
    ref: str


class ExprStep(_Model):
    """Arithmetic over columns, written in SQL: `MARC.BSTMI * MARM_EA.UMREN`,
    `round(MBEW.STPRS / MARM_EA.UMREN, 2)`. A blank operand gives a blank
    result (SQL NULL propagation)."""
    op: Literal["expr"]
    expr: str


class CaseWhen(_Model):
    """`value` sets a constant; `steps` computes the result from the incoming
    value (an empty list keeps it unchanged)."""
    when: Condition
    value: str | None = None
    steps: list["TransformStep"] | None = None

    @model_validator(mode="after")
    def _one(self) -> "CaseWhen":
        if (self.value is None) == (self.steps is None):
            raise ValueError("a case needs exactly one of 'value' or 'steps'")
        return self


class CaseStep(_Model):
    """Split rule: the first matching case sets the value; if none match,
    `otherwise` steps run on the incoming value (e.g. a crosswalk lookup)."""
    op: Literal["case"]
    rule_id: str | None = None
    cases: list[CaseWhen] = Field(min_length=1)
    otherwise: list["TransformStep"] = Field(default_factory=list)


class RouteCase(_Model):
    when: Condition
    value: str


class RouteBranch(_Model):
    """One routing pass over the ECC rows: the first matching case names the
    target; rows no case matches produce nothing from this branch."""
    name: str | None = None
    guard: Condition | None = None       # applies to every case of the branch
    cases: list[RouteCase] = Field(min_length=1)


class RouteStep(_Model):
    """Conditional fan-out of a key field (plant routing). Every branch is
    evaluated; each ECC row becomes one row per distinct target it gets.
    Rows with no target are not migrated. Only allowed as the first step of
    a key field."""
    op: Literal["route"]
    branches: list[RouteBranch] = Field(min_length=1)


TransformStep = Annotated[
    Union[
        StripStep, UpperStep, LowerStep, StripLeadingZerosStep, ZfillStep,
        ValueMapStep, DefaultStep, ConstantStep, NumberStep, DateStep, CaseStep,
        FromRefStep, ExprStep, RouteStep,
    ],
    Field(discriminator="op"),
]
CaseWhen.model_rebuild()
CaseStep.model_rebuild()


def iter_steps(steps: list) -> list:
    """All steps, including those nested in case results and case.otherwise."""
    out = []
    for s in steps:
        out.append(s)
        if isinstance(s, CaseStep):
            for c in s.cases:
                out.extend(iter_steps(c.steps or []))
            out.extend(iter_steps(s.otherwise))
    return out


def iter_conditions(steps: list) -> list[Condition]:
    out: list[Condition] = []
    for s in iter_steps(steps):
        if isinstance(s, CaseStep):
            out += [c.when for c in s.cases]
        elif isinstance(s, RouteStep):
            for b in s.branches:
                out += ([b.guard] if b.guard else []) + [c.when for c in b.cases]
    return out


_EXPR_REF = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")


def step_refs(steps: list) -> set[str]:
    """Technical references (ALIAS.FIELD) used by steps, incl. conditions."""
    refs: set[str] = set()
    for s in iter_steps(steps):
        if isinstance(s, FromRefStep):
            refs.add(s.ref)
        elif isinstance(s, ExprStep):
            refs |= {f"{a}.{b}".upper() for a, b in _EXPR_REF.findall(s.expr)}
    for c in iter_conditions(steps):
        refs |= c.sap_refs()
    return refs


# -- mapping building blocks ----------------------------------------------------

class EccSource(_Model):
    table: str | None = None
    field: str | None = None     # ECC technical field name, e.g. WERKS
    note: str | None = None      # e.g. DERIVED_WERKS


class FieldSpec(_Model):
    id: str
    s4_label: str
    s4_column: str | None = None       # exact S/4 header (technical name)
    ecc_column: str | None = None      # exact ECC header as stored in the snapshot
    ecc_source: EccSource = Field(default_factory=EccSource)
    group: str | None = None
    mandatory: bool = False
    data_type: Literal["Text", "Number", "Date"] | None = None
    length: int | float | None = None
    decimal: int | float | None = None
    # technical source, e.g. MARC.DISMM; resolved to an extract column via the
    # dictionary when ecc_column is not set explicitly
    source_ref: str | None = None
    transform: list[TransformStep] = Field(default_factory=list)   # ECC value -> expected S/4 value
    compare: list[TransformStep] = Field(default_factory=list)     # applied to both sides before comparing
    # reference material the rule came from (workbook text, SQL per plant block);
    # informational, shown in the rule book
    reference: dict[str, Any] | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class KeySpec(_Model):
    fields: list[str] = Field(min_length=1)
    provenance: Provenance = Field(default_factory=Provenance)


class FilterSpec(_Model):
    id: str
    side: Side
    expr: Condition
    source_text: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class ValueMapEntry(_Model):
    from_: str = Field(alias="from")
    to: str
    provenance: Provenance = Field(default_factory=Provenance)


class ValueMap(_Model):
    """Crosswalk from ECC values to S/4 values. Several entries with the same
    `from` make a fan-out: one ECC row becomes one S/4 row per target (e.g.
    plant 1021 -> US27 and US30). Fan-out maps may only be used by key fields."""
    description: str | None = None
    on_missing: Literal["flag", "passthrough", "default"] = "flag"
    default: str | None = None
    entries: list[ValueMapEntry] = Field(default_factory=list)

    def lookup(self) -> dict[str, str]:
        return {e.from_: e.to for e in self.entries}

    def targets(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for e in self.entries:
            out.setdefault(e.from_, [])
            if e.to not in out[e.from_]:
                out[e.from_].append(e.to)
        return out

    def is_fanout(self) -> bool:
        return any(len(t) > 1 for t in self.targets().values())


class LookupKey(_Model):
    left: str                        # ref on the source side, e.g. MARC.MATNR
    right: str                       # technical field of the looked-up table, e.g. MATNR
    normalize: list[TransformStep] = Field(default_factory=list)


class LookupWhere(_Model):
    field: str                       # technical field of the looked-up table
    op: Literal["eq", "ne", "in", "not_in", "is_null", "not_null"]
    value: str | None = None
    values: list[str] | None = None


class LookupSpec(_Model):
    """Left join of a supporting table (MARA, MARM, MARD, ...) onto the ECC
    rows. Its columns become refs `<id>.<FIELD>`. The first matching row is
    used. If the table isn't available, rules using it report "input
    missing" instead of failing."""
    id: str                          # alias used in refs, e.g. MARA, MARM_EA
    table: str                       # dataset name, e.g. MARA, MARM, MARC
    keys: list[LookupKey] = Field(min_length=1)
    where: list[LookupWhere] = Field(default_factory=list)
    columns: list[str] = Field(min_length=1)
    description: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class IgnoredColumn(_Model):
    column: str
    reason: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class IgnoredColumns(_Model):
    ecc: list[IgnoredColumn] = Field(default_factory=list)
    s4: list[IgnoredColumn] = Field(default_factory=list)


class MappingDoc(_Model):
    schema_version: int = 1
    object: str
    title: str | None = None
    s4_sheet: str | None = None
    mapping_version: int = 1
    content_hash: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None
    key: KeySpec
    filters: list[FilterSpec] = Field(default_factory=list)
    raw_condition_notes: str | None = None
    value_maps: dict[str, ValueMap] = Field(default_factory=dict)
    ignored_columns: IgnoredColumns = Field(default_factory=IgnoredColumns)
    # alias of this object's own ECC table in refs (MARC.BESKZ); defaults to `object`
    source_alias: str | None = None
    # technical -> extract column label, per table: {"MARC": {"BESKZ": "Procurement type"}}
    dictionary: dict[str, dict[str, str]] = Field(default_factory=dict)
    lookups: list[LookupSpec] = Field(default_factory=list)
    fields: list[FieldSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _cross_refs(self) -> "MappingDoc":
        ids = [f.id for f in self.fields]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate field ids: {sorted(dupes)}")
        known = set(ids)
        missing_keys = [k for k in self.key.fields if k not in known]
        if missing_keys:
            raise ValueError(f"key refers to unknown field(s): {missing_keys}")
        lookup_ids = [lk.id.upper() for lk in self.lookups]
        if len(lookup_ids) != len(set(lookup_ids)):
            raise ValueError("duplicate lookup ids")
        aliases = {self.alias()} | set(lookup_ids)

        def check_refs(refs: set[str], where: str) -> None:
            for r in refs:
                prefix, _, rest = r.partition(".")
                if prefix.upper() == "TARGET":
                    if rest not in self.key.fields:
                        raise ValueError(f"{where}: TARGET.{rest} must name a key field")
                elif prefix.upper() not in aliases:
                    raise ValueError(f"{where}: ref '{r}' uses unknown alias '{prefix}' "
                                     f"(known: {sorted(aliases)})")

        for lk in self.lookups:
            check_refs({k.left for k in lk.keys}, f"lookup '{lk.id}'")
        for f in self.fields:
            for step in iter_steps(f.transform) + iter_steps(f.compare):
                if isinstance(step, ValueMapStep) and step.map not in self.value_maps:
                    raise ValueError(f"field '{f.id}' uses unknown value map '{step.map}'")
                if isinstance(step, RouteStep) and (f.id not in self.key.fields or step is not f.transform[0]):
                    raise ValueError(f"routing must be the first transform step of a key field (used by '{f.id}')")
            for i, step in enumerate(f.transform):
                vm = self.value_maps.get(step.map) if isinstance(step, ValueMapStep) else None
                if vm is not None and vm.is_fanout() and (f.id not in self.key.fields or i != 0):
                    raise ValueError(f"fan-out map '{step.map}' must be the first transform step of a key field "
                                     f"(used by '{f.id}')")
            for c in iter_conditions(f.transform):
                bad = c.field_refs() - known
                if bad:
                    raise ValueError(f"field '{f.id}' rule refers to unknown field(s) {sorted(bad)}")
            check_refs(step_refs(f.transform) | ({f.source_ref} if f.source_ref else set()), f"field '{f.id}'")
        filter_ids = [flt.id for flt in self.filters]
        if len(filter_ids) != len(set(filter_ids)):
            raise ValueError("duplicate filter ids")
        for flt in self.filters:
            bad = flt.expr.field_refs() - known
            if bad:
                raise ValueError(f"filter '{flt.id}' refers to unknown field(s) {sorted(bad)}")
            check_refs(flt.expr.sap_refs(), f"filter '{flt.id}'")
        return self

    # -- convenience lookups ------------------------------------------------

    def field(self, field_id: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.id == field_id), None)

    def key_fields(self) -> list[FieldSpec]:
        return [self.field(k) for k in self.key.fields]  # type: ignore[misc]

    def column_of(self, field_id: str, side: Side) -> str | None:
        f = self.field(field_id)
        if f is None:
            return None
        return self.ecc_column_of(f) if side == "ecc" else f.s4_column

    def own_ecc_column_of(self, f: "FieldSpec") -> str | None:
        """Column of this object's own ECC extract the field reads, if any
        (explicit ecc_column, or its source_ref resolved via the dictionary)."""
        if f.ecc_column:
            return f.ecc_column
        if not f.source_ref:
            return None
        prefix, _, fld = f.source_ref.partition(".")
        if prefix.upper() != self.alias():
            return None
        label = (self.dictionary.get(self.alias()) or {}).get(fld.upper())
        if label:
            return label
        return next((x.ecc_column for x in self.fields
                     if x.ecc_column and (x.ecc_source.field or "").upper() == fld.upper()), None)

    def ecc_column_of(self, f: "FieldSpec") -> str | None:
        """Any column the field reads: own extract column, or a lookup column
        (`MARA.MTART`) when its source is a supporting table."""
        own = self.own_ecc_column_of(f)
        if own or not f.source_ref:
            return own
        prefix, _, fld = f.source_ref.partition(".")
        return f"{prefix.upper()}.{fld.upper()}" if self.lookup(prefix) else None

    def has_source(self, f: "FieldSpec") -> bool:
        return bool(f.ecc_column or f.source_ref)

    def is_derived_only(self, f: "FieldSpec") -> bool:
        """No source column, but a rule produces the value (constant, lookup, ...)."""
        return not self.has_source(f) and bool(f.transform)

    def ignored(self, side: Side) -> set[str]:
        return {c.column for c in getattr(self.ignored_columns, side)}

    def alias(self) -> str:
        return (self.source_alias or self.object).upper()

    def lookup(self, lookup_id: str) -> LookupSpec | None:
        return next((lk for lk in self.lookups if lk.id.upper() == lookup_id.upper()), None)

    def routed_fields(self) -> list[str]:
        """Key fields whose first step is routing (conditional fan-out)."""
        out = []
        for fid in self.key.fields:
            f = self.field(fid)
            if f and f.transform and isinstance(f.transform[0], RouteStep):
                out.append(fid)
        return out

    def fanout_fields(self) -> dict[str, str]:
        """{field_id: map_id} for key fields whose first step is a fan-out map."""
        out = {}
        for fid in self.key.fields:
            f = self.field(fid)
            if f and f.transform and isinstance(f.transform[0], ValueMapStep):
                vm = self.value_maps.get(f.transform[0].map)
                if vm and vm.is_fanout():
                    out[fid] = f.transform[0].map
        return out
