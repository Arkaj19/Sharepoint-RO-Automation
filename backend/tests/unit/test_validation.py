import pandas as pd

from app.mapping.model import NumberStep, StripLeadingZerosStep
from app.validation.service import validate
from tests.conftest import synthetic_doc


def _doc():
    doc = synthetic_doc()
    for fid, col in (("product_number", "Material"), ("plant", "Plant"), ("mrp_type", "MRP Type"),
                     ("procurement_type", "Procurement type"), ("safety_stock", "Safety Stock")):
        doc.field(fid).ecc_column = col
    doc.field("product_number").compare = [StripLeadingZerosStep(op="strip_leading_zeros")]
    doc.field("safety_stock").compare = [NumberStep(op="number")]
    return doc


def _frames():
    ecc = pd.DataFrame({"Material": ["1", "2", "3"], "Plant": ["P1", "P1", "P1"], "MRP Type": ["PD", "VB", "PD"],
                        "Procurement type": ["E", "F", "F"], "Safety Stock": ["1", "2", "3"]})
    s4 = pd.DataFrame({"PRODUCT": ["001", "002", "003"], "werks": ["P1", "P1", "P1"], "DISMM": ["PD", "VB", "PD"],
                       "BESKZ": ["E", "F", "F"], "EISBE": ["1.0", "2.0", "3.0"]})
    return ecc, s4


def test_clean_pair_passes_with_normalised_compare():
    ecc, s4 = _frames()
    report = validate("TEST", ecc, s4, doc=_doc())
    assert report.overall_status.value == "pass", [(r.rule_id, r.summary) for r in report.rules]


def test_duplicate_keys_and_mismatches_do_not_crash():
    ecc, s4 = _frames()
    s4 = pd.concat([s4, s4.iloc[[0]]], ignore_index=True)       # duplicate key in S/4
    s4.loc[1, "DISMM"] = "ZZ"
    report = validate("TEST", ecc, s4, doc=_doc())
    by = {r.rule_id: r for r in report.rules}
    assert by["key_integrity"].status.value == "fail"
    assert by["value_transformation"].status.value == "fail"
    assert {"field": "mrp_type", "mismatch_count": 1} in by["value_transformation"].details


def test_unmapped_mapping_reports_warnings_not_false_passes():
    ecc, s4 = _frames()
    report = validate("TEST", ecc, s4, doc=synthetic_doc())      # no ECC columns mapped yet
    by = {r.rule_id: r for r in report.rules}
    assert by["field_coverage"].status.value == "warning"
    assert by["key_integrity"].status.value == "warning"
    assert by["value_transformation"].status.value == "warning"
