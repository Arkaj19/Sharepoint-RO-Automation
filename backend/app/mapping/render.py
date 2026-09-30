"""
Human-readable text for mapping logic. Shared by the UI (proposal cards,
mapping view) and the rule book, so both describe a rule the same way.

`logic(doc, field)` produces the rule book's Logic Type (Passthrough /
Hardcoded / Derived) and Logic text in the style of the business rule
workbook: bullets of `condition -> result`, per-plant sections where the
rule differs by plant, value maps written out inline.
"""
from __future__ import annotations

from app.mapping.model import (
    CaseStep, Condition, ConstantStep, DateStep, DefaultStep, ExprStep, FieldSpec, FilterSpec,
    FromRefStep, LookupSpec, LowerStep, MappingDoc, NumberStep, RouteStep, StripLeadingZerosStep, StripStep,
    UpperStep, ValueMapStep, ZfillStep,
)

ARROW = "→"


def _name(c: Condition) -> str:
    if c.ref:
        return c.ref
    return f"{c.field}{' (target)' if c.on == 'target' else ''}"


def condition(c: Condition | None) -> str:
    if c is None:
        return ""
    if c.all is not None:
        return " AND ".join(f"({condition(x)})" if x.any else condition(x) for x in c.all)
    if c.any is not None:
        return " OR ".join(condition(x) for x in c.any)
    name = _name(c)
    if c.op == "eq":
        return f"{name} = '{c.value}'"
    if c.op == "ne":
        return f"{name} <> '{c.value}'"
    if c.op == "in":
        return f"{name} IN ({', '.join(repr(v) for v in c.values or [])})"
    if c.op == "not_in":
        return f"{name} NOT IN ({', '.join(repr(v) for v in c.values or [])})"
    if c.op == "is_null":
        return f"{name} IS BLANK"
    return f"{name} IS NOT BLANK"


def step(s) -> str:
    if isinstance(s, StripStep):
        return "trim"
    if isinstance(s, UpperStep):
        return "upper-case"
    if isinstance(s, LowerStep):
        return "lower-case"
    if isinstance(s, StripLeadingZerosStep):
        return "strip leading zeros"
    if isinstance(s, ZfillStep):
        return f"pad with zeros to {s.width}"
    if isinstance(s, ValueMapStep):
        return f"map via '{s.map}'"
    if isinstance(s, DefaultStep):
        return f"default '{s.value}'" + (" (always)" if s.when == "always" else " when blank")
    if isinstance(s, ConstantStep):
        return f"'{s.value}'"
    if isinstance(s, NumberStep):
        return "canonical number" + (f" ({s.decimals} dp)" if s.decimals is not None else "")
    if isinstance(s, DateStep):
        return f"date -> {s.to_format}"
    if isinstance(s, FromRefStep):
        return s.ref
    if isinstance(s, ExprStep):
        return s.expr
    if isinstance(s, RouteStep):
        return f"routing ({len(s.branches)} branch(es))"
    if isinstance(s, CaseStep):
        parts = [f"IF {condition(c.when)} THEN {_result(c)}" for c in s.cases]
        tail = f" ELSE {steps(s.otherwise)}" if s.otherwise else " ELSE unchanged"
        return "; ".join(parts) + tail
    return str(s)


def _result(c) -> str:
    if c.value is not None:
        return f"'{c.value}'"
    return steps(c.steps or []) if c.steps else "unchanged"


def steps(ss: list) -> str:
    return " -> ".join(step(s) for s in ss) if ss else "copy as is"


def lookup_text(lk: LookupSpec | None) -> str | None:
    if lk is None:
        return None
    keys = " AND ".join(f"{k.left} = {lk.table}.{k.right}" for k in lk.keys)
    where = (" WHERE " + " AND ".join(f"{w.field} {w.op} {w.value or w.values or ''}".strip() for w in lk.where)
             if lk.where else "")
    return f"{lk.id}: {lk.table} ON {keys}{where} -> {', '.join(lk.columns)}"


def field_summary(f: FieldSpec, doc: MappingDoc | None = None) -> dict:
    return {
        "id": f.id,
        "s4_label": f.s4_label,
        "s4_column": f.s4_column,
        "ecc_column": doc.ecc_column_of(f) if doc else f.ecc_column,
        "source_ref": f.source_ref,
        "transform": steps(f.transform),
        "compare": steps(f.compare) if f.compare else None,
        "type": f.data_type,
        "length": f.length,
        "mandatory": f.mandatory,
        "status": f.provenance.status,
    }


def filter_text(flt: FilterSpec) -> str:
    return f"[{flt.side.upper()}] keep rows where {condition(flt.expr)}"


# -- rule book logic -------------------------------------------------------------------------

def _source_label(doc: MappingDoc, f: FieldSpec) -> str:
    if f.source_ref:
        return f.source_ref
    if f.ecc_source.table and f.ecc_source.field:
        return f"{f.ecc_source.table}.{f.ecc_source.field}"
    return doc.ecc_column_of(f) or "source"


def _bullets(doc: MappingDoc, f: FieldSpec, ss: list, indent: str = "") -> list[str]:
    """Bullet lines for a step chain."""
    src = _source_label(doc, f)
    if not ss:
        return [f"{indent}• {src}"]
    if len(ss) == 1 and isinstance(ss[0], CaseStep):
        c = ss[0]
        lines = []
        for case in c.cases:
            lines.append(f"{indent}• {condition(case.when)} {ARROW} {_case_result(doc, f, case)}")
        lines.append(f"{indent}• Else {ARROW} {_chain(doc, f, c.otherwise)}")
        return lines
    if len(ss) == 1 and isinstance(ss[0], ValueMapStep):
        vm = doc.value_maps.get(ss[0].map)
        if vm:
            lines = [f"{indent}• {src} {e_from} {ARROW} {', '.join(tos)}"
                     for e_from, tos in vm.targets().items()]
            miss = {"flag": "flagged as unmapped", "passthrough": "kept as is",
                    "default": f"'{vm.default}'"}[vm.on_missing]
            return lines + [f"{indent}• Other values {ARROW} {miss}"]
    return [f"{indent}• {_chain(doc, f, ss)}"]


def _case_result(doc: MappingDoc, f: FieldSpec, case) -> str:
    if case.value is not None:
        return f"'{case.value}'"
    return _chain(doc, f, case.steps or [])


def _chain(doc: MappingDoc, f: FieldSpec, ss: list) -> str:
    if not ss:
        return _source_label(doc, f)
    if len(ss) == 1 and isinstance(ss[0], (ConstantStep, FromRefStep, ExprStep)):
        return step(ss[0])
    if len(ss) == 1 and isinstance(ss[0], CaseStep):
        c = ss[0]
        inner = "; ".join(f"{condition(x.when)} {ARROW} {_case_result(doc, f, x)}" for x in c.cases)
        return f"[{inner}; else {ARROW} {_chain(doc, f, c.otherwise)}]"
    start = _source_label(doc, f) if not isinstance(ss[0], (ConstantStep, FromRefStep, ExprStep)) else ""
    return " -> ".join(([start] if start else []) + [step(s) for s in ss])


def _plant_sections(doc: MappingDoc, f: FieldSpec) -> list[str] | None:
    """A top-level case whose cases are 'source plant in (...)' blocks renders
    as per-plant sections."""
    if len(f.transform) != 1 or not isinstance(f.transform[0], CaseStep):
        return None
    c = f.transform[0]
    if not all(case.steps is not None and case.when.ref and case.when.op in ("in", "eq")
               and case.when.ref.split(".")[-1] in ("WERKS", "BWKEY") for case in c.cases):
        return None
    lines = []
    for case in c.cases:
        plants = case.when.values or [case.when.value]
        lines.append(f"For plant {', '.join(plants)}:")
        lines += _bullets(doc, f, case.steps or [], "   ")
    if c.otherwise:
        lines.append("Other plants:")
        lines += _bullets(doc, f, c.otherwise, "   ")
    return lines


def _route_lines(doc: MappingDoc, r: RouteStep) -> list[str]:
    lines = []
    for b in r.branches:
        head = f"{b.name or 'Branch'}" + (f" (when {condition(b.guard)})" if b.guard else "")
        lines.append(head + ":")
        for case in b.cases:
            lines.append(f"   • {condition(case.when)} {ARROW} '{case.value}'")
    lines.append("Each ECC row goes to every plant it is routed to; rows with no route are not migrated.")
    return lines


def logic(doc: MappingDoc, f: FieldSpec) -> tuple[str, str]:
    """(Logic Type, Logic text) for the rule book."""
    t = f.transform
    if t and isinstance(t[0], RouteStep):
        return "Derived", "\n".join(_route_lines(doc, t[0]))
    if not t:
        if not doc.has_source(f):
            return "N/A", "No source mapped"
        return "Passthrough", ""
    if len(t) == 1 and isinstance(t[0], ConstantStep):
        return "Hardcoded", f"'{t[0].value}'"
    sections = _plant_sections(doc, f)
    lines = sections if sections is not None else _bullets(doc, f, t)
    if f.compare:
        lines.append(f"(compared after: {steps(f.compare)})")
    return "Derived", "\n".join(lines)
