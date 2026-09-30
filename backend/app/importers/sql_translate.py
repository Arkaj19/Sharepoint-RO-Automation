"""
Databricks SQL -> mapping rules.

Reads the per-plant views in backend/reference_logic/databricks/*.sql
(CREATE OR REPLACE VIEW ... mm_S_MARC / mm_S_MBEW) and translates their
expressions into the YAML rule vocabulary:

  column                      -> source_ref / from_ref
  'literal'                   -> constant
  CASE WHEN ... THEN ... END  -> case (conditions with SQL NULL semantics)
  a * b, round(x / y, 2)      -> expr
  REGEXP_REPLACE(x,'^0+','')  -> strip_leading_zeros
  to_date(x [, fmt])          -> date
  base.derived_werks          -> TARGET.<plant field> (the routed plant)
  base CTE CASE derived_werks -> route branches (plant routing)

Anything outside this vocabulary raises Untranslatable, and the importer
reports the field as "needs a rule" instead of guessing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

from app.mapping.model import (
    CaseStep, CaseWhen, Condition, ConstantStep, DateStep, ExprStep, FromRefStep, RouteBranch, RouteCase,
    StripLeadingZerosStep,
)

BASE_ALIASES = {"BASE"}
_ALIAS_FIX = {"EKGRP_UPD": "EKGRP_UPD", "MARA_NEW": "MARA_NEW", "MARM_EA": "MARM_EA"}
_COMMENTED_WHEN = re.compile(r"^(\s*)--\s*(when\b.*\bthen\b.*)$", re.IGNORECASE)


class Untranslatable(ValueError):
    pass


@dataclass
class Block:
    object: str                 # MARC | MBEW
    name: str                   # e.g. plant_1021
    file: str
    plants: list[str]
    sql: str
    tree: exp.Expression
    variant_sql: str | None = None       # base CTE with commented-out WHEN lines restored
    variant_tree: exp.Expression | None = None
    restored_lines: list[str] = field(default_factory=list)

    def mapped(self, variant: bool = False) -> exp.Select:
        tree = self.variant_tree if variant and self.variant_tree is not None else self.tree
        return _cte(tree, "mapped")

    def base(self, variant: bool = False) -> exp.Expression | None:
        tree = self.variant_tree if variant and self.variant_tree is not None else self.tree
        try:
            return _cte(tree, "base")
        except KeyError:
            return None


def _cte(tree: exp.Expression, name: str):
    for c in tree.find_all(exp.CTE):
        if c.alias.lower() == name:
            node = c.this
            return node if isinstance(node, exp.Select) else node.find(exp.Select) if name == "mapped" else node
    raise KeyError(name)


def _clean(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not re.match(r"^-{5,}", l.strip()))


def _restore_commented_whens(text: str) -> tuple[str, list[str]]:
    """Uncomments `-- when ... then ...` lines inside the base CTE only."""
    low = text.lower()
    start = low.find("with base as")
    end = low.find("mapped as")
    if start < 0 or end < 0:
        return text, []
    head, body, tail = text[:start], text[start:end], text[end:]
    restored = []
    out_lines = []
    for line in body.splitlines(keepends=True):
        eol = line[len(line.rstrip("\r\n")):]
        m = _COMMENTED_WHEN.match(line.rstrip("\r\n"))
        if m:
            restored.append(m.group(2).strip())
            out_lines.append(m.group(1) + m.group(2) + eol)
        else:
            out_lines.append(line)
    return head + "".join(out_lines) + tail, restored


def _plants(select: exp.Select, alias_field: tuple[str, str]) -> list[str]:
    table, fld = alias_field
    where = select.args.get("where")
    out: list[str] = []
    if where is None:
        return out
    for node in where.find_all(exp.EQ, exp.In):
        col = node.this
        if isinstance(col, exp.Column) and col.name.upper() == fld and (col.table or "").upper() == table:
            if isinstance(node, exp.EQ) and isinstance(node.expression, exp.Literal):
                out.append(node.expression.this)
            elif isinstance(node, exp.In) and not node.args.get("query"):
                out += [e.this for e in node.expressions if isinstance(e, exp.Literal)]
    return out


def load_blocks(folder: Path) -> list[Block]:
    blocks = []
    for f in sorted(folder.glob("*.sql")):
        text = f.read_text(encoding="utf-8")
        for i, part in enumerate(re.split(r"^%sql\s*$", text, flags=re.M)[1:]):
            sql = _clean(part)
            tree = sqlglot.parse_one(sql, read="databricks")
            view = tree.this.sql().lower() if isinstance(tree, exp.Create) else ""
            obj = "MARC" if view.endswith("mm_s_marc") else "MBEW" if view.endswith("mm_s_mbew") else None
            if obj is None:
                continue
            b = Block(object=obj, name=f.stem, file=f.name, plants=[], sql=sql, tree=tree)
            b.plants = _plants(b.mapped(), ("MARC", "WERKS") if obj == "MARC" else ("MBEW", "BWKEY"))
            vtext, restored = _restore_commented_whens(sql)
            if restored:
                b.variant_sql, b.restored_lines = vtext, restored
                b.variant_tree = sqlglot.parse_one(vtext, read="databricks")
            blocks.append(b)
    return blocks


class Translator:
    """Translates SQL expressions of one object into rule steps/conditions."""

    def __init__(self, alias: str, plant_field: str, default_table: str | None = None):
        self.alias = alias.upper()
        self.plant_field = plant_field
        self.default_table = default_table

    # -- references -------------------------------------------------------------------------
    def ref(self, col: exp.Column) -> str:
        table = (col.table or self.default_table or self.alias).upper()
        name = col.name.upper()
        if table in BASE_ALIASES:
            if name == "DERIVED_WERKS":
                return f"TARGET.{self.plant_field}"
            return f"{self.alias}.{name}" if self.alias == "MARC" or name != "WERKS" else f"{self.alias}.BWKEY"
        return f"{_ALIAS_FIX.get(table, table)}.{name}"

    def _col(self, node) -> exp.Column | None:
        while isinstance(node, (exp.Paren, exp.Cast)):
            node = node.this
        return node if isinstance(node, exp.Column) else None

    # -- conditions -----------------------------------------------------------------------------
    def _leaf(self, col: exp.Column, **kw) -> Condition:
        r = self.ref(col)
        if r.startswith("TARGET."):
            return Condition(field=r.split(".", 1)[1], on="target", **kw)
        return Condition(ref=r, **kw)

    @staticmethod
    def _lit(node) -> tuple[str, bool] | None:
        if isinstance(node, exp.Literal):
            numeric = (not node.is_string) or bool(re.match(r"^-?\d+\.\d+$", node.this))
            return node.this, numeric
        if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
            return "-" + node.this.this, True
        return None

    def cond(self, e) -> Condition:
        if isinstance(e, exp.Paren):
            return self.cond(e.this)
        if isinstance(e, exp.And):
            parts = []
            for side in (e.this, e.expression):
                c = self.cond(side)
                parts += c.all if c.all is not None else [c]
            return Condition(all=parts)
        if isinstance(e, exp.Or):
            parts = []
            for side in (e.this, e.expression):
                c = self.cond(side)
                parts += c.any if c.any is not None else [c]
            return Condition(any=parts)
        if isinstance(e, exp.Not):
            return negate(self.cond(e.this))
        if isinstance(e, (exp.EQ, exp.NEQ)):
            col, lit = self._col(e.this), self._lit(e.expression)
            if col is None or lit is None:
                col, lit = self._col(e.expression), self._lit(e.this)
            if col is None or lit is None:
                raise Untranslatable(f"comparison not against a literal: {e.sql()}")
            value, numeric = lit
            if isinstance(e, exp.EQ):
                # SQL: NULL = '' is not true; a blank in the extract stands for NULL
                return self._leaf(col, op="eq", value=value, numeric=numeric, sql_null=True)
            if value == "" and not numeric:
                return self._leaf(col, op="not_null")
            return self._leaf(col, op="ne", value=value, numeric=numeric, sql_null=True)
        if isinstance(e, exp.In):
            col = self._col(e.this)
            if col is None or e.args.get("query"):
                raise Untranslatable(f"IN over a sub-query: {e.sql()[:120]}")
            vals = [self._lit(x) for x in e.expressions]
            if any(v is None for v in vals):
                raise Untranslatable(f"IN list with non-literals: {e.sql()}")
            return self._leaf(col, op="in", values=[v[0] for v in vals])
        if isinstance(e, exp.Is):
            col = self._col(e.this)
            if col is None or not isinstance(e.expression, exp.Null):
                raise Untranslatable(f"IS test: {e.sql()}")
            return self._leaf(col, op="is_null")
        raise Untranslatable(f"condition not supported: {e.sql()[:160]}")

    # -- values ---------------------------------------------------------------------------------
    def primary_source(self, e) -> str | None:
        """The column an expression reads when no rule applies (its ELSE)."""
        while isinstance(e, (exp.Paren, exp.Cast, exp.Alias)):
            e = e.this
        if isinstance(e, exp.Column):
            r = self.ref(e)
            return None if r.startswith("TARGET.") else r
        if isinstance(e, (exp.RegexpReplace, exp.TsOrDsToDate, exp.StrToDate)):
            return self.primary_source(e.this)
        if isinstance(e, exp.Case) and e.args.get("default") is not None:
            return self.primary_source(e.args["default"])
        return None

    def _expr_text(self, e) -> str:
        def norm(node):
            if isinstance(node, exp.Column):
                r = self.ref(node)
                t, n = r.split(".", 1)
                return exp.column(n, table=t)
            return node
        return e.transform(norm).sql(dialect="databricks")

    def steps(self, e, src: str | None) -> list:
        if isinstance(e, exp.Alias):
            return self.steps(e.this, src)
        if isinstance(e, (exp.Paren, exp.Cast)):
            return self.steps(e.this, src)
        if isinstance(e, exp.Column):
            r = self.ref(e)
            return [] if r == src else [FromRefStep(op="from_ref", ref=r)]
        if isinstance(e, exp.Null):
            return [ConstantStep(op="constant", value="")]
        lit = self._lit(e)
        if lit is not None:
            return [ConstantStep(op="constant", value=lit[0])]
        if isinstance(e, exp.RegexpReplace):
            pat = e.expression.this if isinstance(e.expression, exp.Literal) else None
            rep = e.args.get("replacement")
            if pat == "^0+" and (rep is None or (isinstance(rep, exp.Literal) and rep.this == "")):
                return self.steps(e.this, src) + [StripLeadingZerosStep(op="strip_leading_zeros")]
            raise Untranslatable(f"regexp_replace: {e.sql()}")
        if isinstance(e, (exp.TsOrDsToDate, exp.StrToDate)):
            fmt = e.args.get("format")
            formats = []
            if isinstance(fmt, exp.Literal):
                formats.append(fmt.this.replace("strict", ""))
            base = DateStep(op="date")
            return self.steps(e.this, src) + [DateStep(op="date", formats=formats + base.formats)]
        if isinstance(e, exp.Case):
            cases = []
            for i in e.args.get("ifs", []):
                when = self.cond(i.this)
                then = i.args.get("true")
                lit_t = self._lit(then) if then is not None else None
                if isinstance(then, exp.Null):
                    cases.append(CaseWhen(when=when, value=""))
                elif lit_t is not None:
                    cases.append(CaseWhen(when=when, value=lit_t[0]))
                else:
                    cases.append(CaseWhen(when=when, steps=self.steps(then, src)))
            default = e.args.get("default")
            otherwise = self.steps(default, src) if default is not None else [ConstantStep(op="constant", value="")]
            return [CaseStep(op="case", cases=cases, otherwise=otherwise)]
        if isinstance(e, (exp.Mul, exp.Div, exp.Add, exp.Sub, exp.Round, exp.Neg)):
            return [ExprStep(op="expr", expr=self._expr_text(e))]
        raise Untranslatable(f"expression not supported: {e.sql()[:160]}")

    # -- routing ----------------------------------------------------------------------------------
    def route_branches(self, block: Block, plant_cond: Condition, variant: bool = False) -> list[RouteBranch]:
        base = block.base(variant)
        branches = []
        label = ", ".join(block.plants) or block.name
        if base is None:
            proj = next((p for p in block.mapped(variant).expressions
                         if p.alias_or_name.upper() in ("WERKS", "BWKEY")), None)
            lit = self._lit(proj.this) if proj is not None else None
            if lit is None:
                raise Untranslatable(f"{block.name}: no base CTE and no fixed plant")
            return [RouteBranch(name=f"{label}: fixed", guard=plant_cond,
                                cases=[RouteCase(when=plant_cond, value=lit[0])])]
        selects = [s for s in base.find_all(exp.Select)
                   if any(p.alias_or_name.lower() == "derived_werks" for p in s.expressions)]
        for n, sel in enumerate(selects, start=1):
            proj = next(p for p in sel.expressions if p.alias_or_name.lower() == "derived_werks")
            case = proj.this
            if not isinstance(case, exp.Case):
                raise Untranslatable(f"{block.name}: derived_werks is not a CASE")
            guards = [plant_cond]
            where = sel.args.get("where")
            if where is not None:
                guards.append(self.cond(where.this))
            for j in sel.args.get("joins") or []:
                if j.side == "" and j.kind in ("INNER", "") and j.args.get("on") is not None:
                    table = (j.this.alias or j.this.name).upper()
                    guards.append(Condition(ref=f"{table}.MATNR", op="not_null"))
                    extra = [c for c in _conjuncts(j.args["on"]) if not _is_equi(c)]
                    guards += [self.cond(c) for c in extra]
            cases = []
            for i in case.args.get("ifs", []):
                lit = self._lit(i.args.get("true"))
                if lit is None:
                    raise Untranslatable(f"{block.name}: routing THEN is not a literal")
                cases.append(RouteCase(when=self.cond(i.this), value=lit[0]))
            branches.append(RouteBranch(name=f"{label}: branch {n}", guard=Condition(all=guards), cases=cases))
        return branches


def _conjuncts(e) -> list:
    if isinstance(e, exp.Paren):
        return _conjuncts(e.this)
    if isinstance(e, exp.And):
        return _conjuncts(e.this) + _conjuncts(e.expression)
    return [e]


def _is_equi(e) -> bool:
    return isinstance(e, exp.EQ) and isinstance(e.this, (exp.Column, exp.Anonymous, exp.RegexpReplace)) \
        and isinstance(e.expression, (exp.Column, exp.RegexpReplace))


def negate(c: Condition) -> Condition:
    if c.all is not None:
        return Condition(any=[negate(x) for x in c.all])
    if c.any is not None:
        return Condition(all=[negate(x) for x in c.any])
    flip = {"eq": "ne", "ne": "eq", "in": "not_in", "not_in": "in", "is_null": "not_null", "not_null": "is_null"}
    data = c.model_dump(exclude_none=True)
    data["op"] = flip[c.op]
    data["sql_null"] = data["op"] in ("ne", "not_in")
    return Condition(**data)


def where_conjuncts(select: exp.Select) -> list:
    where = select.args.get("where")
    return _conjuncts(where.this) if where is not None else []


def sql_of(node) -> str:
    return node.sql(dialect="databricks", pretty=False)
