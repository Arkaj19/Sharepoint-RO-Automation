"""Lookups, routing, expressions, SQL NULL semantics, input missing."""
import pandas as pd
import pytest

from app.mapping.model import (
    CaseStep, CaseWhen, Condition, ExprStep, FieldSpec, FromRefStep, KeySpec, LookupKey, LookupSpec, LookupWhere,
    MappingDoc, RouteBranch, RouteCase, RouteStep, StripLeadingZerosStep,
)
from app.transforms import engine

SLZ = [StripLeadingZerosStep(op="strip_leading_zeros")]


def _doc(route=True) -> MappingDoc:
    plant_t = [RouteStep(op="route", branches=[
        RouteBranch(name="A", guard=Condition(ref="MARC.WERKS", op="eq", value="1021"),
                    cases=[RouteCase(when=Condition(ref="MARC.BESKZ", op="in", values=["E", "F"]), value="US30")]),
        RouteBranch(name="B", guard=Condition(ref="MARC.WERKS", op="eq", value="1021"),
                    cases=[RouteCase(when=Condition(ref="MARC.BESKZ", op="eq", value="F", sql_null=True),
                                     value="US27")]),
    ])] if route else []
    return MappingDoc(
        object="MARC",
        key=KeySpec(fields=["product", "plant"]),
        dictionary={"MARC": {"MATNR": "Material", "WERKS": "Plant", "BESKZ": "Procurement type", "BSTMI": "Min"},
                    "MARA": {"MATNR": "Material", "MTART": "Material Type"},
                    "MARM": {"MATNR": "Material", "MEINH": "Alt UoM", "UMREN": "Denominator"}},
        lookups=[
            LookupSpec(id="MARA", table="MARA", keys=[LookupKey(left="MARC.MATNR", right="MATNR", normalize=SLZ)],
                       columns=["MATNR", "MTART"]),
            LookupSpec(id="MARM_EA", table="MARM", keys=[LookupKey(left="MARC.MATNR", right="MATNR", normalize=SLZ)],
                       where=[LookupWhere(field="MEINH", op="eq", value="EA")], columns=["UMREN"]),
        ],
        fields=[
            FieldSpec(id="product", s4_label="Product", s4_column="PRODUCT", source_ref="MARC.MATNR"),
            FieldSpec(id="plant", s4_label="Plant", s4_column="werks", source_ref="MARC.WERKS", transform=plant_t),
            FieldSpec(id="proc", s4_label="Proc", s4_column="BESKZ", source_ref="MARC.BESKZ", transform=[
                CaseStep(op="case", cases=[CaseWhen(when=Condition(all=[
                    Condition(ref="MARC.BESKZ", op="eq", value="E"),
                    Condition(field="plant", on="target", op="eq", value="US30")]), value="F")])]),
            FieldSpec(id="min_lot", s4_label="Min", s4_column="BSTMI", source_ref="MARC.BSTMI", transform=[
                CaseStep(op="case", cases=[CaseWhen(when=Condition(ref="MARA.MTART", op="eq", value="FERT"),
                                                    steps=[ExprStep(op="expr", expr="MARC.BSTMI * MARM_EA.UMREN")])])]),
            FieldSpec(id="mtart", s4_label="Type", s4_column="MTART", transform=[FromRefStep(op="from_ref", ref="MARA.MTART")]),
            FieldSpec(id="prctr", s4_label="Profit center", s4_column="PRCTR",
                      transform=[FromRefStep(op="from_ref", ref="TARGET.plant")]),
        ],
    )


ECC = pd.DataFrame({"Material": ["1", "2", "3", "4"], "Plant": ["1021", "1021", "1021", "1099"],
                    "Procurement type": ["E", "F", "", "F"], "Min": ["2", "3", "4", "5"]})
REFS = {
    "MARA": pd.DataFrame({"Material": ["0001", "0002", "0003"], "Material Type": ["FERT", "ROH", "FERT"]}),
    "MARM": pd.DataFrame({"Material": ["1", "1", "2"], "Alt UoM": ["EA", "CS", "EA"], "Denominator": ["6", "1", "12"]}),
}


def test_lookups_routing_and_rules():
    doc = _doc()
    df = engine.prepare_ecc(doc, ECC, REFS)
    # material 1 (E) -> US30 only; 2 (F) -> US30 and US27; 3 (blank) -> none; 4 is another plant -> none
    got = list(zip(df["Material"], df[engine.target_col("plant")]))
    assert got == [("1", "US30"), ("2", "US30"), ("2", "US27")]
    assert engine.expected_s4(doc, doc.field("proc"), df).values.tolist() == ["F", "F", "F"]
    # FERT: 2 * 6 (EA of material 1); ROH keeps the value
    assert engine.expected_s4(doc, doc.field("min_lot"), df).values.tolist() == ["12", "3", "3"]
    assert engine.expected_s4(doc, doc.field("mtart"), df).values.tolist() == ["FERT", "ROH", "ROH"]
    assert engine.expected_s4(doc, doc.field("prctr"), df).values.tolist() == ["US30", "US30", "US27"]


def test_missing_lookup_table_reports_input_missing():
    doc = _doc(route=False)
    df = engine.prepare_ecc(doc, ECC, {"MARA": None, "MARM": REFS["MARM"]})
    assert engine.missing_inputs(df) == ["MARA"]
    with pytest.raises(engine.MissingInput):
        engine.expected_s4(doc, doc.field("mtart"), df)


def test_sql_null_semantics_and_numeric_compare():
    doc = _doc(route=False)
    df = pd.DataFrame({"Procurement type": ["", "X", "F"], "Min": ["0.000", "0", "1.5"]})
    ne = Condition(ref="MARC.BESKZ", op="ne", value="F", sql_null=True)
    assert engine.eval_condition(ne, df, doc, "ecc").tolist() == [False, True, False]
    eq_blank = Condition(ref="MARC.BESKZ", op="eq", value="", sql_null=True)     # SQL: NULL = '' is not true
    assert engine.eval_condition(eq_blank, df, doc, "ecc").tolist() == [False, False, False]
    num = Condition(ref="MARC.BSTMI", op="eq", value="0.000", numeric=True)
    assert engine.eval_condition(num, df, doc, "ecc").tolist() == [True, True, False]


def test_expressions_round_and_blank_propagation():
    doc = _doc(route=False)
    df = pd.DataFrame({"Min": ["10", "", "7"], "MARM_EA.UMREN": ["3", "2", ""]})
    out = engine.eval_expr("round(MARC.BSTMI / MARM_EA.UMREN, 2)", df, doc)
    assert out.tolist() == ["3.33", "", ""]


def test_comparable_ignores_formatting():
    f = FieldSpec(id="x", s4_label="x")
    doc = MappingDoc(object="T", key=KeySpec(fields=["x"]), fields=[f])
    vals = pd.Series(["000123", "1.500", "2026-01-31 00:00:00", "US30", ""])
    assert engine.comparable(vals, f, doc).tolist() == ["123", "1.5", "2026-01-31", "US30", ""]


def test_comparable_is_one_normalisation_for_raw_or_prenormalised_text():
    from app.mapping.model import DateStep
    f = FieldSpec(id="d", s4_label="d", data_type="Date", compare=[DateStep(op="date")])
    doc = MappingDoc(object="T", key=KeySpec(fields=["d"]), fields=[f])
    raw = pd.Series(["2026-01-31 00:00:00", "20260131", "31.01.2026", "", "00000000"])
    assert engine.comparable(raw, f, doc).tolist() == \
        engine.comparable(engine.normalize_series(raw), f, doc).tolist()
