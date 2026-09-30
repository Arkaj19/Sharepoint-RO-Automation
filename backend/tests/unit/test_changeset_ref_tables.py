"""
A refresh in which only a supporting table changed must produce an incremental
change set that records that table's change, tagged with the lookup and the
fields whose rules read it - not an empty "baseline (first run)".
"""
import pandas as pd

from app.diff import changeset
from app.mapping import repository
from app.mapping.model import (
    CaseStep, CaseWhen, Condition, ExprStep, FieldSpec, KeySpec, LookupKey, LookupSpec, LookupWhere, MappingDoc,
    StripLeadingZerosStep,
)
from tests.conftest import put_snapshot


def _doc() -> MappingDoc:
    return MappingDoc(
        object="MARC", key=KeySpec(fields=["product", "plant"]),
        dictionary={"MARC": {"MATNR": "Material", "WERKS": "Plant", "BSTMI": "Min"},
                    "MARM": {"MATNR": "Material", "MEINH": "Alt UoM", "UMREN": "Denominator"}},
        lookups=[LookupSpec(id="MARM_EA", table="MARM",
                            keys=[LookupKey(left="MARC.MATNR", right="MATNR",
                                            normalize=[StripLeadingZerosStep(op="strip_leading_zeros")])],
                            where=[LookupWhere(field="MEINH", op="eq", value="EA")], columns=["UMREN"])],
        fields=[FieldSpec(id="product", s4_label="Product", s4_column="PRODUCT", source_ref="MARC.MATNR"),
                FieldSpec(id="plant", s4_label="Plant", s4_column="werks", source_ref="MARC.WERKS"),
                FieldSpec(id="min_lot", s4_label="Min", s4_column="BSTMI", source_ref="MARC.BSTMI", transform=[
                    CaseStep(op="case", cases=[CaseWhen(when=Condition(ref="MARC.WERKS", op="eq", value="1"),
                                                        steps=[ExprStep(op="expr",
                                                                        expr="MARC.BSTMI * MARM_EA.UMREN")])])]),
                FieldSpec(id="unrelated", s4_label="X", s4_column="X", source_ref="MARC.WERKS")])


def _setup():
    repository.save(_doc(), updated_by="test", bump=False)
    ecc = pd.DataFrame({"Material": ["1", "2"], "Plant": ["1", "1"], "Min": ["2", "3"]})
    s4 = pd.DataFrame({"PRODUCT": ["1", "2"], "werks": ["1", "1"], "BSTMI": ["12", "36"], "X": ["1", "1"]})
    put_snapshot("MARC", "ECC", ecc)
    put_snapshot("MARC", "S4", s4)
    marm = pd.DataFrame({"Material": ["1", "1", "2"], "Alt UoM": ["EA", "CS", "EA"], "Denominator": ["6", "1", "12"]})
    put_snapshot("REF", "MARM", marm)
    return marm


def test_ref_only_change_is_recorded_and_incremental(env):
    marm = _setup()
    first = changeset.build("MARC")
    assert first["baseline"] is True                      # the object's very first change set

    changed = marm.copy()
    changed.loc[0, "Denominator"] = "12"
    put_snapshot("REF", "MARM", changed)
    cs = changeset.build("MARC", dataset_status={"ECC": "unchanged", "S4": "unchanged"},
                         ref_status={"MARM": "changed", "MARA": "unchanged"})
    assert cs["baseline"] is False
    assert cs["datasets"]["REF/MARM"]["lookups"] == ["MARM_EA"]
    cells = [c for c in cs["changes"] if c["kind"] == "cells_changed"]
    assert len(cells) == 1 and cells[0]["side"] == "REF/MARM"
    col = cells[0]["columns"][0]
    assert col["column"] == "Denominator" and col["samples"][0]["before"] == "6" and col["samples"][0]["after"] == "12"
    assert cells[0]["impacts"] == ["field:min_lot", "lookup:MARM_EA"]


def test_unused_ref_table_change_is_ignored(env):
    _setup()
    changeset.build("MARC")
    cs = changeset.build("MARC", dataset_status={"ECC": "unchanged", "S4": "unchanged"},
                         ref_status={"MARA": "changed"})           # MARC has no MARA lookup here
    assert cs["baseline"] is False and cs["changes"] == [] and "REF/MARA" not in cs["datasets"]


def test_workbook_change_is_announced(env):
    _setup()
    changeset.build("MARC")
    wb = pd.DataFrame({"SAP Structure": ["S_MARC"], "SAP Field": ["KOKRS"], "Logic": ["CG01"]})
    put_snapshot("REF", "RULE_WORKBOOK", wb)
    wb.loc[0, "Logic"] = "CG02"
    put_snapshot("REF", "RULE_WORKBOOK", wb)
    cs = changeset.build("MARC", dataset_status={"ECC": "unchanged", "S4": "unchanged"},
                         ref_status={"RULE_WORKBOOK": "changed"})
    assert [c["kind"] for c in cs["changes"]] == ["workbook_changed"] and "Re-import" in cs["changes"][0]["hint"]
