import pandas as pd

from app.mapping.model import (
    CaseStep, CaseWhen, Condition, DateStep, FilterSpec, NumberStep, StripLeadingZerosStep, ValueMap,
    ValueMapEntry, ValueMapStep, ZfillStep,
)
from app.transforms import engine
from tests.conftest import synthetic_doc


def _vm(pairs, **kw):
    return ValueMap(entries=[ValueMapEntry(**{"from": a, "to": b}) for a, b in pairs], **kw)


def test_basic_steps():
    doc = synthetic_doc()
    s = pd.Series(["000123", "0", " 42 ", "", "ABC"])
    assert engine.apply_steps(s, [StripLeadingZerosStep(op="strip_leading_zeros")], doc=doc).values.tolist() == \
        ["123", "0", "42", "", "ABC"]
    assert engine.apply_steps(pd.Series(["123", ""]), [ZfillStep(op="zfill", width=6)], doc=doc).values.tolist() == \
        ["000123", ""]
    assert engine.apply_steps(pd.Series(["1.0", "0.000", "12.50", "x", "1,000"]), [NumberStep(op="number")],
                              doc=doc).values.tolist() == ["1", "0", "12.5", "x", "1000"]
    assert engine.apply_steps(pd.Series(["31.12.2024", "2024-12-31 00:00:00"]), [DateStep(op="date")],
                              doc=doc).values.tolist() == ["2024-12-31", "2024-12-31"]


def test_value_map_missing_modes():
    doc = synthetic_doc()
    doc.value_maps["m"] = _vm([("A", "X")], on_missing="flag")
    res = engine.apply_steps(pd.Series(["A", "B", ""]), [ValueMapStep(op="value_map", map="m")], doc=doc)
    assert res.values.tolist() == ["X", "B", ""]
    assert res.unmapped.tolist() == [False, True, False]
    doc.value_maps["m"] = _vm([("A", "X")], on_missing="default", default="Z")
    res = engine.apply_steps(pd.Series(["A", "B", ""]), [ValueMapStep(op="value_map", map="m")], doc=doc)
    assert res.values.tolist() == ["X", "Z", ""]


def test_fanout_and_target_condition():
    doc = synthetic_doc()
    doc.field("plant").ecc_column = "Plant"
    doc.field("procurement_type").ecc_column = "Procurement type"
    doc.value_maps["plant_crosswalk"] = _vm([("1021", "US27"), ("1021", "US30"), ("1025", "US29")])
    doc.field("plant").transform = [ValueMapStep(op="value_map", map="plant_crosswalk")]
    doc.field("procurement_type").transform = [CaseStep(op="case", cases=[CaseWhen(
        when=Condition(all=[Condition(field="procurement_type", op="eq", value="E"),
                            Condition(field="plant", on="target", op="eq", value="US30")]), value="F")])]
    ecc = pd.DataFrame({"Plant": ["1021", "1025", "1099"], "Procurement type": ["E", "E", "F"]})
    prepared = engine.prepare_ecc(doc, ecc)
    assert len(prepared) == 4                                  # 1021 fans out into two rows
    plant = engine.expected_s4(doc, doc.field("plant"), prepared)
    assert plant.values.tolist() == ["US27", "US30", "US29", "1099"]
    assert plant.unmapped.tolist() == [False, False, False, True]
    proc = engine.expected_s4(doc, doc.field("procurement_type"), prepared)
    assert proc.values.tolist() == ["E", "F", "E", "F"]


def test_filters_report_rows_removed():
    doc = synthetic_doc()
    doc.field("plant").ecc_column = "Plant"
    doc.filters = [FilterSpec(id="f1", side="ecc", expr=Condition(field="plant", op="in", values=["1021"]))]
    kept, report = engine.apply_filters(pd.DataFrame({"Plant": ["1021", "1099", "1021"]}), doc, "ecc")
    assert len(kept) == 2 and report == [{"filter_id": "f1", "rows_removed": 1, "samples": [{"plant": "1099"}]}]
