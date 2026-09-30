"""
Executes the YAML mapping's rules on pandas data. This is the only
interpreter of mapping logic: generation, validation, the inference helpers
and Agent 1's dry-run tools all call it, so the rule book, the checks and
the agent's evidence can never disagree.

All values are handled as strings (snapshots are read with dtype=str);
"blank" means empty after stripping and plays the role of SQL NULL.

Preparing the ECC rows (`prepare_ecc`) happens in three passes:
  1. lookups   - supporting tables (MARA, MARM, MARD, ...) are left-joined
                 on; their columns become refs such as `MARA.MTART`;
  2. routing   - a key field whose first step is `route` (plant routing)
                 copies each ECC row once per target it is routed to; rows
                 with no target are not migrated;
  3. fan-out   - a key field whose first step is a value map with several
                 targets per source value copies rows likewise.
The routed / fanned-out value is kept in a `__target__<field>` column.

A rule that needs an input that isn't available (a lookup table that has
not been delivered, a column the dictionary can't resolve) raises
MissingInput, so callers can report "input missing" instead of failing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache

import numpy as np
import pandas as pd

from app.mapping.model import (
    CaseStep, Condition, ConstantStep, DateStep, DefaultStep, ExprStep, FieldSpec, FromRefStep,
    LowerStep, MappingDoc, NumberStep, RouteStep, Side, StripLeadingZerosStep, StripStep, UpperStep,
    ValueMapStep, ZfillStep,
)

ECC_ROW_COL = "__ecc_row"
MISSING_ATTR = "missing_inputs"


class MissingInput(KeyError):
    """A rule needs data that isn't available (yet)."""


def target_col(field_id: str) -> str:
    return f"__target__{field_id}"


def unmapped_col(field_id: str) -> str:
    return f"__unmapped__{field_id}"


def lookup_col(lookup_id: str, field: str) -> str:
    return f"{lookup_id.upper()}.{field.upper()}"


@dataclass
class StepResult:
    values: pd.Series
    unmapped: pd.Series        # True where a value map had no entry (on_missing=flag)


def as_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str)


def blank_mask(series: pd.Series) -> pd.Series:
    return as_text(series).str.strip() == ""


# -- scalar helpers (applied once per distinct value) -----------------------------

def canonical_number(text: str, decimals: int | None = None) -> str:
    t = text.strip().replace(",", "")
    if t == "":
        return text
    try:
        d = Decimal(t)
    except InvalidOperation:
        return text
    if decimals is not None:
        return f"{d:.{decimals}f}"
    if d == 0:
        return "0"
    return format(d.normalize(), "f")


def canonical_date(text: str, formats: list[str], to_format: str) -> str:
    t = text.strip()
    if t == "":
        return text
    for fmt in formats:
        try:
            return datetime.strptime(t, fmt).strftime(to_format)
        except ValueError:
            continue
    return text


def _map_distinct(series: pd.Series, fn) -> pd.Series:
    lookup = {u: fn(u) for u in series.unique()}
    return series.map(lookup)


_NUM_TEXT = re.compile(r"^-?\d+(?:[.,]\d+)?$|^-?[.,]\d+$")
_DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$|^\d{2}[./]\d{2}[./]\d{4}$")
_DATE_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%Y%m%d"]


def normalize_value(v: str) -> str:
    """Formatting-insensitive form used for every comparison: trimmed,
    numbers canonical ('0.000' = '0', '000123' = '123'), dates ISO."""
    t = v.strip()
    if t == "":
        return ""
    if _DATE_TEXT.match(t):
        return canonical_date(t, _DATE_FORMATS, "%Y-%m-%d")
    if _NUM_TEXT.match(t):
        return canonical_number(t)
    return t


def normalize_series(series: pd.Series) -> pd.Series:
    return _map_distinct(as_text(series), normalize_value)


# -- column resolution ---------------------------------------------------------------

def dictionary_label(doc: MappingDoc, table: str, field: str) -> str | None:
    return (doc.dictionary.get(table.upper()) or {}).get(field.upper())


def ecc_column_name(doc: MappingDoc, f: FieldSpec) -> str | None:
    """The extract column a field reads from (see MappingDoc.ecc_column_of)."""
    return doc.ecc_column_of(f)


def resolve_ref(frame: pd.DataFrame, doc: MappingDoc, ref: str) -> pd.Series:
    """Text values of `ALIAS.FIELD` (or `TARGET.<field id>`) on the frame."""
    prefix, _, fld = ref.partition(".")
    p = prefix.upper()
    if p == "TARGET":
        col = target_col(fld)
        if col not in frame.columns:
            f = doc.field(fld)
            if f is None:
                raise KeyError(f"unknown key field '{fld}'")
            return expected_s4(doc, f, frame).values
        return as_text(frame[col])
    if p == doc.alias():
        label = dictionary_label(doc, p, fld)
        candidates = [c for c in (label, fld, fld.upper()) if c]
        candidates += [x.ecc_column for x in doc.fields
                       if x.ecc_column and (x.ecc_source.field or "").upper() == fld.upper()]
        for c in candidates:
            if c in frame.columns:
                return as_text(frame[c])
        raise MissingInput(f"{ref}: no ECC column (add it to the {p} dictionary)"
                           if not label else f"{ref}: ECC column '{label}' not in the data")
    if doc.lookup(p):
        col = lookup_col(p, fld)
        if col in frame.columns:
            return as_text(frame[col])
        missing = frame.attrs.get(MISSING_ATTR, [])
        table = doc.lookup(p).table
        if table in missing:
            raise MissingInput(f"{ref}: table {table} is not available")
        raise MissingInput(f"{ref}: column {fld} was not brought in by lookup {p}")
    raise KeyError(f"unknown ref '{ref}'")


def field_source_values(doc: MappingDoc, f: FieldSpec, frame: pd.DataFrame) -> pd.Series | None:
    """Incoming ECC values of a field, or None when it has no source at all
    (a purely derived / hardcoded field)."""
    if f.ecc_column:
        if f.ecc_column not in frame.columns:
            raise KeyError(f"field '{f.id}' has no ECC column in this data")
        return frame[f.ecc_column]
    if f.source_ref:
        return resolve_ref(frame, doc, f.source_ref)
    return None


# -- lookups ---------------------------------------------------------------------------

def _table_column(doc: MappingDoc, table: str, field: str, df: pd.DataFrame) -> str | None:
    label = dictionary_label(doc, table, field)
    for c in (label, field, field.upper()):
        if c and c in df.columns:
            return c
    return None


def _where_mask(df: pd.DataFrame, col: str, w) -> pd.Series:
    vals = as_text(df[col]).str.strip()
    if w.op == "eq":
        return vals == (w.value or "")
    if w.op == "ne":
        return vals != (w.value or "")
    if w.op == "in":
        return vals.isin(w.values or [])
    if w.op == "not_in":
        return ~vals.isin(w.values or [])
    if w.op == "is_null":
        return vals == ""
    return vals != ""


def enrich(doc: MappingDoc, frame: pd.DataFrame, refs: dict[str, pd.DataFrame | None]) -> tuple[pd.DataFrame, list[str]]:
    """Left-joins every lookup table onto the frame. Returns the frame and
    the tables that were not available."""
    missing: list[str] = []
    for lk in doc.lookups:
        table = refs.get(lk.table)
        if table is None:
            missing.append(lk.table)
            continue
        t = table
        try:
            for w in lk.where:
                col = _table_column(doc, lk.table, w.field, t)
                if col is None:
                    raise MissingInput(f"lookup {lk.id}: {lk.table}.{w.field} not in the table")
                t = t[_where_mask(t, col, w)]
            key_names, left_vals, right_vals = [], [], []
            for i, k in enumerate(lk.keys):
                rcol = _table_column(doc, lk.table, k.right, t)
                if rcol is None:
                    raise MissingInput(f"lookup {lk.id}: {lk.table}.{k.right} not in the table")
                lv = resolve_ref(frame, doc, k.left)
                rv = as_text(t[rcol])
                if k.normalize:
                    lv = apply_steps(lv, k.normalize, doc=doc).values
                    rv = apply_steps(rv, k.normalize, doc=doc).values
                key_names.append(f"__lk{i}")
                left_vals.append(lv.str.strip())
                right_vals.append(rv.str.strip())
            cols = {}
            for fld in lk.columns:
                c = _table_column(doc, lk.table, fld, t)
                if c is not None:
                    cols[lookup_col(lk.id, fld)] = as_text(t[c]).to_numpy()
            right = pd.DataFrame({**{n: v.to_numpy() for n, v in zip(key_names, right_vals)}, **cols})
            right = right.drop_duplicates(key_names)
            left = pd.DataFrame({n: v.to_numpy() for n, v in zip(key_names, left_vals)}, index=frame.index)
            joined = left.merge(right, on=key_names, how="left")
            joined.index = frame.index
            for c in cols:
                frame[c] = joined[c].fillna("")
        except MissingInput:
            missing.append(lk.table)
    return frame, missing


# -- preparation (lookups, routing, fan-out) -------------------------------------------------

def prepare_ecc(doc: MappingDoc, ecc_df: pd.DataFrame, refs: dict | None = None) -> pd.DataFrame:
    """Adds __ecc_row, joins lookups, expands routing and fan-out. Idempotent:
    an already prepared frame is returned unchanged."""
    if ECC_ROW_COL in ecc_df.columns:
        return ecc_df
    df = ecc_df.reset_index(drop=True).copy()
    df[ECC_ROW_COL] = range(len(df))
    missing: list[str] = []
    if doc.lookups:
        if refs is None:
            from app.transforms.context import load_refs
            refs = load_refs(doc)
        df, missing = enrich(doc, df, refs)
    df.attrs[MISSING_ATTR] = missing

    for fid in doc.routed_fields():
        step: RouteStep = doc.field(fid).transform[0]  # type: ignore[union-attr,assignment]
        pieces = []
        for branch in step.branches:
            target = pd.Series(np.nan, index=df.index, dtype=object)
            guard = eval_condition(branch.guard, df, doc, "ecc") if branch.guard else pd.Series(True, index=df.index)
            for case in branch.cases:
                m = guard & target.isna() & eval_condition(case.when, df, doc, "ecc")
                target = target.where(~m, case.value)
            pieces.append(pd.DataFrame({"__pos": np.arange(len(df)), "__t": target.to_numpy()}))
        routed = pd.concat(pieces).dropna(subset=["__t"]).drop_duplicates()
        routed = routed.sort_values(["__pos"], kind="stable")
        attrs = df.attrs
        df = df.iloc[routed["__pos"].to_numpy()].reset_index(drop=True)
        df.attrs = attrs
        df[target_col(fid)] = routed["__t"].to_numpy()
        df[unmapped_col(fid)] = False

    for fid, map_id in doc.fanout_fields().items():
        f = doc.field(fid)
        col = ecc_column_name(doc, f) if f else None
        if not f or not col or col not in df.columns:
            continue
        pairs = [(src, tgt) for src, tgts in doc.value_maps[map_id].targets().items() for tgt in tgts]
        table = pd.DataFrame(pairs, columns=["__src_key", target_col(fid)])
        attrs = df.attrs
        df["__src_key"] = as_text(df[col]).str.strip()
        df = df.merge(table, on="__src_key", how="left")
        df.attrs = attrs
        missing_map = df[target_col(fid)].isna()
        df[unmapped_col(fid)] = missing_map & (df["__src_key"] != "")
        df[target_col(fid)] = df[target_col(fid)].fillna(df["__src_key"])
        df = df.drop(columns="__src_key")
        df.attrs = attrs
    return df


def missing_inputs(frame: pd.DataFrame) -> list[str]:
    return list(frame.attrs.get(MISSING_ATTR, []))


# -- conditions ------------------------------------------------------------------------

def source_values(frame: pd.DataFrame, doc: MappingDoc, field_id: str, side: Side) -> pd.Series:
    f = doc.field(field_id)
    if f is None:
        raise KeyError(f"unknown field '{field_id}'")
    if side == "s4":
        if not f.s4_column or f.s4_column not in frame.columns:
            raise KeyError(f"field '{field_id}' has no S4 column in this data")
        return as_text(frame[f.s4_column]).str.strip()
    vals = field_source_values(doc, f, frame)
    if vals is None:
        raise KeyError(f"field '{field_id}' has no ECC column in this data")
    return as_text(vals).str.strip()


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.str.replace(",", "", regex=False), errors="coerce")


def eval_condition(cond: Condition, frame: pd.DataFrame, doc: MappingDoc, side: Side,
                   _active: frozenset = frozenset()) -> pd.Series:
    if cond.all is not None:
        mask = pd.Series(True, index=frame.index)
        for c in cond.all:
            mask &= eval_condition(c, frame, doc, side, _active)
        return mask
    if cond.any is not None:
        mask = pd.Series(False, index=frame.index)
        for c in cond.any:
            mask |= eval_condition(c, frame, doc, side, _active)
        return mask
    if cond.ref:
        vals = as_text(resolve_ref(frame, doc, cond.ref)).str.strip()
    elif cond.on == "target" and side == "ecc":
        vals = _target_values(frame, doc, cond.field, _active)  # type: ignore[arg-type]
    else:
        vals = source_values(frame, doc, cond.field, side)  # type: ignore[arg-type]
    blank = vals == ""
    if cond.op == "is_null":
        return blank
    if cond.op == "not_null":
        return ~blank
    wanted = [cond.value] if cond.op in ("eq", "ne") else list(cond.values or [])
    if cond.numeric:
        nv = _num(vals)
        targets = [float(w) for w in wanted if w not in (None, "")]
        hit = nv.isin(targets) if targets else pd.Series(False, index=frame.index)
    else:
        hit = vals.isin([w or "" for w in wanted])
    if cond.op in ("eq", "in"):
        return hit & ~blank if cond.sql_null else hit
    out = ~hit
    return out & ~blank if cond.sql_null else out


def _target_values(frame: pd.DataFrame, doc: MappingDoc, field_id: str, active: frozenset) -> pd.Series:
    if target_col(field_id) in frame.columns:
        return as_text(frame[target_col(field_id)]).str.strip()
    if field_id in active:
        raise ValueError(f"circular target condition on field '{field_id}'")
    f = doc.field(field_id)
    if f is None:
        raise KeyError(f"unknown field '{field_id}'")
    return expected_s4(doc, f, frame, _active=active | {field_id}).values.str.strip()


def _row_keys(rows: pd.DataFrame, doc: MappingDoc, side: Side) -> list[dict]:
    """{key field: value} of some rows, read from the side's own key columns."""
    cols = {}
    for f in doc.key_fields():
        c = doc.own_ecc_column_of(f) if side == "ecc" else f.s4_column
        if c and c in rows.columns:
            cols[f.id] = c
    return [{fid: str(r[c]) for fid, c in cols.items()} for _, r in rows.iterrows()]


def apply_filters(frame: pd.DataFrame, doc: MappingDoc, side: Side) -> tuple[pd.DataFrame, list[dict]]:
    """Keeps rows that pass every filter for `side`. Returns the frame and a
    per-filter report (rows removed, or why it couldn't run)."""
    report = []
    keep = pd.Series(True, index=frame.index)
    for flt in doc.filters:
        if flt.side != side:
            continue
        try:
            mask = eval_condition(flt.expr, frame, doc, side)
        except MissingInput as exc:
            report.append({"filter_id": flt.id, "input_missing": str(exc).strip("'\"")})
            continue
        except (KeyError, ValueError) as exc:
            report.append({"filter_id": flt.id, "error": str(exc)})
            continue
        removed = keep & ~mask
        entry = {"filter_id": flt.id, "rows_removed": int(removed.sum())}
        if entry["rows_removed"]:
            entry["samples"] = _row_keys(frame[removed].head(5), doc, side)
        report.append(entry)
        keep &= mask
    attrs = frame.attrs
    out = frame[keep]
    out.attrs = attrs
    return out, report


# -- expressions --------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _parse_expr(expr: str):
    import sqlglot
    return sqlglot.parse_one(expr, read="databricks")


def _eval_expr_node(node, frame: pd.DataFrame, doc: MappingDoc):
    from sqlglot import exp
    if isinstance(node, exp.Paren):
        return _eval_expr_node(node.this, frame, doc)
    if isinstance(node, exp.Cast):
        return _eval_expr_node(node.this, frame, doc)
    if isinstance(node, exp.Column):
        vals = resolve_ref(frame, doc, f"{node.table.upper()}.{node.name.upper()}")
        return _num(vals.str.strip())
    if isinstance(node, exp.Literal):
        return float(node.this) if not node.is_string else pd.to_numeric(node.this, errors="coerce")
    if isinstance(node, exp.Neg):
        return -_eval_expr_node(node.this, frame, doc)
    if isinstance(node, exp.Round):
        dec = int(node.args["decimals"].this) if node.args.get("decimals") is not None else 0
        v = _eval_expr_node(node.this, frame, doc)
        return v.round(dec) if isinstance(v, pd.Series) else round(v, dec)
    ops = {exp.Mul: np.multiply, exp.Div: np.divide, exp.Add: np.add, exp.Sub: np.subtract}
    for kind, fn in ops.items():
        if isinstance(node, kind):
            a = _eval_expr_node(node.this, frame, doc)
            b = _eval_expr_node(node.expression, frame, doc)
            with np.errstate(divide="ignore", invalid="ignore"):
                out = fn(a, b)
            if isinstance(out, pd.Series):
                return out.replace([np.inf, -np.inf], np.nan)
            return out
    raise ValueError(f"unsupported expression: {node.sql()}")


def _format_number(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    return canonical_number(format(float(v), ".10f"))


def eval_expr(expr: str, frame: pd.DataFrame, doc: MappingDoc) -> pd.Series:
    out = _eval_expr_node(_parse_expr(expr), frame, doc)
    if not isinstance(out, pd.Series):
        out = pd.Series(out, index=frame.index)
    return out.map(_format_number)


# -- transform steps ---------------------------------------------------------------------

def apply_steps(values: pd.Series, steps: list, *, doc: MappingDoc,
                frame: pd.DataFrame | None = None, side: Side = "ecc",
                _active: frozenset = frozenset()) -> StepResult:
    """Runs `steps` over `values`. `frame` (the whole prepared source table,
    same index) is needed by rules that look at other columns."""
    vals = as_text(values)
    unmapped = pd.Series(False, index=vals.index)
    for step in steps:
        if isinstance(step, StripStep):
            vals = vals.str.strip()
        elif isinstance(step, UpperStep):
            vals = vals.str.upper()
        elif isinstance(step, LowerStep):
            vals = vals.str.lower()
        elif isinstance(step, StripLeadingZerosStep):
            stripped = vals.str.strip()
            no_zeros = stripped.str.lstrip("0")
            # all-zero values become "0"; blanks stay blank
            vals = no_zeros.where(no_zeros != "", stripped.where(stripped == "", "0"))
        elif isinstance(step, ZfillStep):
            stripped = vals.str.strip()
            vals = stripped.where(stripped == "", stripped.str.zfill(step.width))
        elif isinstance(step, NumberStep):
            vals = _map_distinct(vals, lambda v, d=step.decimals: canonical_number(v, d))
        elif isinstance(step, DateStep):
            vals = _map_distinct(vals, lambda v, s=step: canonical_date(v, s.formats, s.to_format))
        elif isinstance(step, ConstantStep):
            vals = pd.Series(step.value, index=vals.index)
        elif isinstance(step, DefaultStep):
            if step.when == "always":
                vals = pd.Series(step.value, index=vals.index)
            else:
                vals = vals.where(vals.str.strip() != "", step.value)
        elif isinstance(step, ValueMapStep):
            vm = doc.value_maps[step.map]
            key = vals.str.strip()
            mapped = key.map(vm.lookup())
            missing = mapped.isna() & (key != "")
            if vm.on_missing == "default" and vm.default is not None:
                vals = mapped.fillna(vm.default).where(key != "", vals)
            else:
                vals = mapped.fillna(vals)
                if vm.on_missing == "flag":
                    unmapped |= missing
        elif isinstance(step, FromRefStep):
            if frame is None:
                raise ValueError("from_ref needs the source frame")
            vals = as_text(resolve_ref(frame, doc, step.ref)).reindex(vals.index)
        elif isinstance(step, ExprStep):
            if frame is None:
                raise ValueError("expr needs the source frame")
            vals = eval_expr(step.expr, frame, doc).reindex(vals.index)
        elif isinstance(step, CaseStep):
            if frame is None:
                raise ValueError("split rules need the source frame")
            base = apply_steps(vals, step.otherwise, doc=doc, frame=frame, side=side, _active=_active)
            out = base.values.copy()
            matched = pd.Series(False, index=vals.index)
            for case in step.cases:
                m = eval_condition(case.when, frame, doc, side, _active) & ~matched
                if not m.any():
                    matched |= m
                    continue
                if case.value is not None:
                    out = out.where(~m, case.value)
                else:
                    res = apply_steps(vals, case.steps or [], doc=doc, frame=frame, side=side, _active=_active)
                    out = out.where(~m, res.values)
                    unmapped |= res.unmapped & m
                matched |= m
            vals = out
            unmapped |= base.unmapped & ~matched
        elif isinstance(step, RouteStep):
            raise ValueError("routing is applied by prepare_ecc, not as an inner step")
        else:  # pragma: no cover - the model's union is closed
            raise ValueError(f"unsupported step {step!r}")
    return StepResult(values=vals, unmapped=unmapped)


def expected_s4(doc: MappingDoc, field: FieldSpec, ecc_df: pd.DataFrame,
                _active: frozenset = frozenset()) -> StepResult:
    """ECC values of `field` after its transform: what S/4 should contain.
    `ecc_df` should be the output of prepare_ecc()."""
    if target_col(field.id) in ecc_df.columns:
        rest = field.transform[1:]
        res = apply_steps(ecc_df[target_col(field.id)], rest, doc=doc, frame=ecc_df, side="ecc", _active=_active)
        return StepResult(values=res.values, unmapped=res.unmapped | ecc_df[unmapped_col(field.id)].astype(bool))
    if field.id in doc.fanout_fields() or field.id in doc.routed_fields():
        raise ValueError(f"field '{field.id}' fans out - run prepare_ecc() on the ECC frame first")
    incoming = field_source_values(doc, field, ecc_df)
    if incoming is None:
        if not field.transform:
            raise KeyError(f"field '{field.id}' has no ECC column in this data")
        incoming = pd.Series("", index=ecc_df.index)
    return apply_steps(incoming, field.transform, doc=doc, frame=ecc_df, side="ecc", _active=_active | {field.id})


def comparable(values: pd.Series, field: FieldSpec, doc: MappingDoc) -> pd.Series:
    """Normalises values for comparison: the field's own `compare` steps, then
    formatting (canonical numbers and dates). Shared by validation, shadow
    comparison, alignment and the agent's evidence, so they all agree.
    Formatting is normalised before the compare steps too, so a step such as
    `date` sees the same input whichever caller hands it raw text."""
    base = normalize_series(pd.Series(values).reset_index(drop=True) if not isinstance(values, pd.Series)
                            else values)
    if not field.compare:
        return base
    return normalize_series(apply_steps(base, field.compare, doc=doc).values)


KEY_SEP = "␟"


def _join_parts(parts: list[pd.Series]) -> pd.Series:
    out = parts[0]
    for p in parts[1:]:
        out = out + KEY_SEP + p
    return out


def ecc_key_series(doc: MappingDoc, ecc_df: pd.DataFrame) -> pd.Series:
    """Business key of every (prepared) ECC row, as the S/4 row should have it."""
    return _join_parts([comparable(expected_s4(doc, f, ecc_df).values, f, doc) for f in doc.key_fields()])


def s4_key_series(doc: MappingDoc, s4_df: pd.DataFrame) -> pd.Series:
    parts = []
    for f in doc.key_fields():
        if not f.s4_column or f.s4_column not in s4_df.columns:
            raise KeyError(f"key field '{f.id}' has no S/4 column in this data")
        parts.append(comparable(s4_df[f.s4_column], f, doc))
    return _join_parts(parts)


def split_key(label: str) -> list[str]:
    return label.split(KEY_SEP)
