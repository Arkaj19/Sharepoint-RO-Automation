"""
Fields in the imported style read their ECC column through `source_ref` and the
dictionary (MARC.DISPO -> "MRP Controller") and have no ecc_column. Change
tagging and rename following must resolve them the same way the engine does.
"""
import pandas as pd

from app.agents.rulebook_agent import candidates
from app.diff.changeset import tag_impacts
from app.mapping.model import FieldSpec, KeySpec, MappingDoc


def _doc() -> MappingDoc:
    return MappingDoc(
        object="MARC", key=KeySpec(fields=["product", "plant"]),
        dictionary={"MARC": {"MATNR": "Material", "WERKS": "Plant", "DISPO": "MRP Controller", "DISMM": "MRP Type"}},
        fields=[FieldSpec(id="product", s4_label="Product", s4_column="PRODUCT", source_ref="MARC.MATNR"),
                FieldSpec(id="plant", s4_label="Plant", s4_column="werks", source_ref="MARC.WERKS"),
                FieldSpec(id="mrp_controller", s4_label="MRP Controller", s4_column="DISPO", source_ref="MARC.DISPO"),
                FieldSpec(id="mrp_type", s4_label="MRP Type", s4_column="DISMM", source_ref="MARC.DISMM")])


def test_ecc_changes_are_tagged_with_source_ref_fields():
    changes = [{"kind": "cells_changed", "side": "ECC", "severity": "high",
                "columns": [{"column": "MRP Type", "changed": 1}]},
               {"kind": "new_code_values", "side": "ECC", "severity": "high", "column": "MRP Type", "values": {"VB": 1}},
               {"kind": "column_removed", "side": "ECC", "severity": "high", "column": "MRP Controller"},
               {"kind": "cells_changed", "side": "S4", "severity": "high", "columns": [{"column": "DISMM", "changed": 1}]}]
    tag_impacts(changes, _doc())
    assert [c["impacts"] for c in changes] == [["field:mrp_type"], ["field:mrp_type"], ["field:mrp_controller"],
                                               ["field:mrp_type"]]
    assert changes[0]["columns"][0]["field"] == "mrp_type"


def _frames():
    ecc = pd.DataFrame({"Material": [str(100000 + i) for i in range(40)], "Plant": ["1025"] * 40,
                        "MRP Controller Code": [f"C{i % 7}" for i in range(40)],
                        "MRP Type": ["PD"] * 40})
    s4 = pd.DataFrame({"PRODUCT": [str(100000 + i).zfill(18) for i in range(40)], "werks": ["1025"] * 40,
                       "DISPO": [f"C{i % 7}" for i in range(40)], "DISMM": ["PD"] * 40})
    return ecc, s4


def test_ecc_rename_of_a_source_ref_field_updates_the_dictionary():
    ecc, s4 = _frames()
    changeset = {"changes": [{"id": "C1", "kind": "column_renamed", "side": "ECC", "from": "MRP Controller",
                              "to": "MRP Controller Code", "score": 0.95, "strong": True}]}
    cs = candidates.build(_doc(), ecc, s4, changeset)
    ops = [o for o in cs.ops if o.op in ("set_dictionary", "set_column_alias")]
    assert len(ops) == 1, [o.model_dump() for o in cs.ops]
    op = ops[0]
    assert op.op == "set_dictionary" and op.table == "MARC" and op.entries == {"DISPO": "MRP Controller Code"}
    assert op.evidence.get("change_id") == "C1"            # followed because of the rename, not by value luck
    assert "mrp_controller" in op.reason


def test_s4_rename_is_not_also_proposed_as_ignored():
    ecc, s4 = _frames()
    ecc = ecc.rename(columns={"MRP Controller Code": "MRP Controller"})
    s4 = s4.rename(columns={"DISMM": "DISMM_NEW"})
    changeset = {"changes": [{"id": "C1", "kind": "column_renamed", "side": "S4", "from": "DISMM",
                              "to": "DISMM_NEW", "score": 0.92, "strong": True}]}
    cs = candidates.build(_doc(), ecc, s4, changeset)
    kinds = {(o.op, getattr(o, "column", None)) for o in cs.ops}
    assert ("set_column_alias", "DISMM_NEW") in kinds
    assert ("ignore_column", "DISMM_NEW") not in kinds, kinds


def test_alias_keeps_the_rules_origin():
    from app.mapping import ops as mops
    from app.mapping.model import Provenance
    doc = _doc()
    doc.field("mrp_type").provenance = Provenance(status="approved", source="databricks", rule_id="R-MARC-0007",
                                                  confidence=0.99, reason="Passthrough of MARC.DISMM", version=2)
    op = mops.SetColumnAlias(op="set_column_alias", field_id="mrp_type", side="s4", column="DISMM_NEW",
                             confidence=0.92, reason="renamed")
    mops.apply_op(doc, op, Provenance(status="approved", source="agent", version=3, approved_by="tester",
                                      proposal_id="P-1", confidence=0.92))
    p = doc.field("mrp_type").provenance
    assert (p.source, p.rule_id, p.confidence, p.reason) == ("databricks", "R-MARC-0007", 0.99,
                                                             "Passthrough of MARC.DISMM")
    assert (p.version, p.approved_by, p.proposal_id) == (3, "tester", "P-1")


def test_confirming_an_unchanged_rule_keeps_its_id():
    from app.mapping import ops as mops
    from app.mapping.model import ConstantStep, Provenance
    doc = _doc()
    f = doc.field("mrp_type")
    f.transform = [ConstantStep(op="constant", value="PD")]
    f.provenance = Provenance(status="approved", source="databricks", rule_id="R-MARC-0007", confidence=0.6, version=2)
    same = mops.SetTransform(op="set_transform", field_id="mrp_type", transform=[ConstantStep(op="constant", value="PD")],
                             confidence=0.6, reason="confirmed")
    mops.apply_op(doc, same, Provenance(status="approved", source="databricks", version=3, approved_by="t"))
    assert (f.provenance.rule_id, f.provenance.version, f.provenance.approved_by) == ("R-MARC-0007", 3, "t")
    other = mops.SetTransform(op="set_transform", field_id="mrp_type", transform=[ConstantStep(op="constant", value="ND")],
                              confidence=0.9, reason="changed")
    mops.apply_op(doc, other, Provenance(status="approved", source="databricks", version=4))
    assert f.provenance.rule_id != "R-MARC-0007"                  # a real rule change gets a new rule id
