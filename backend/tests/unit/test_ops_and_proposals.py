import pytest

from app.agents.rulebook_agent import candidates
from app.mapping import ops as mops
from app.mapping import repository
from app.proposals import service as proposals
from app.validation.service import validate
from tests.conftest import synthetic_doc


def _headers(ecc, s4):
    return {"ecc": set(ecc.columns), "s4": set(s4.columns)}


def test_validate_ops_catches_bad_column_and_unknown_field(synthetic):
    doc, ecc, s4 = synthetic
    ops = mops.parse_ops([
        {"op": "set_column_alias", "field_id": "mrp_type", "side": "ecc", "column": "No Such Column", "reason": "x"},
        {"op": "set_column_alias", "field_id": "nope", "side": "ecc", "column": "MRP Type", "reason": "x"},
        {"op": "set_column_alias", "field_id": "mrp_type", "side": "ecc", "column": "MRP Type", "reason": "x"},
    ])
    res = mops.validate_ops(doc, ops, _headers(ecc, s4))
    assert [r["ok"] for r in res] == [False, False, True]
    assert "does not exist" in res[0]["errors"][0]
    assert ops[2].preview["after"]["ecc_column"] == "MRP Type"


def test_candidates_bootstrap_the_synthetic_mapping(synthetic):
    doc, ecc, s4 = synthetic
    cs = candidates.build(doc, ecc, s4, {"changes": [], "baseline": True})
    kinds = {(o.op, getattr(o, "field_id", getattr(o, "map_id", None))) for o in cs.ops}
    assert ("set_column_alias", "product_number") in kinds
    assert ("set_transform", "product_number") in kinds            # strip leading zeros compare
    assert ("add_value_map_entries", "plant_crosswalk") in kinds
    assert ("add_split_rule", "procurement_type") in kinds
    assert ("set_transform", "safety_stock") in kinds              # canonical number compare
    assert ("ignore_column", None) in {(o.op, None) for o in cs.ops if o.op == "ignore_column"}
    assert any(o.op == "add_filter" for o in cs.ops)               # plant 1099 is out of scope
    assert all(r["ok"] for r in mops.validate_ops(doc, cs.ops, _headers(ecc, s4)))


def test_accept_all_apply_then_validation_passes(env, synthetic):
    doc, ecc, s4 = synthetic
    repository.save(doc, updated_by="test", bump=False)
    cs = candidates.build(doc, ecc, s4, {"changes": [], "baseline": True})
    p = proposals.create("TEST", cs.ops, run_id=None, changeset_id=None, mode="bootstrap", summary="t",
                         base_mapping_version=1)
    proposals.decide(p["proposal_id"], [{"op_id": o["op_id"], "decision": "accepted"} for o in p["ops"]], by="me")
    p = proposals.apply(p["proposal_id"], by="me")
    assert {o["status"] for o in p["ops"]} == {"applied"}
    assert p["status"] == "closed"
    cur = repository.load("TEST")
    assert cur.mapping_version == 2
    assert cur.field("mrp_type").provenance.status == "approved"
    assert cur.field("mrp_type").provenance.approved_by == "me"
    assert len(repository.read_changelog("TEST")) == len(p["ops"])

    report = validate("TEST", ecc, s4, doc=cur)
    by_rule = {r.rule_id: r for r in report.rules}
    assert by_rule["record_count"].status.value == "pass", by_rule["record_count"].summary
    assert by_rule["key_integrity"].status.value == "pass", by_rule["key_integrity"].details
    assert by_rule["value_transformation"].status.value == "pass", by_rule["value_transformation"].details


def test_rejected_dependency_makes_dependents_stale(env, synthetic):
    doc, ecc, s4 = synthetic
    repository.save(doc, updated_by="test", bump=False)
    cs = candidates.build(doc, ecc, s4, {"changes": [], "baseline": True})
    p = proposals.create("TEST", cs.ops, run_id=None, changeset_id=None, mode="bootstrap", summary="t")
    xw = next(o for o in p["ops"] if o["op"] == "add_value_map_entries")
    decisions = [{"op_id": o["op_id"], "decision": "rejected" if o["op_id"] == xw["op_id"] else "accepted"}
                 for o in p["ops"]]
    proposals.decide(p["proposal_id"], decisions)
    p = proposals.apply(p["proposal_id"])
    dependents = [o for o in p["ops"] if xw["op_id"] in o.get("depends_on", [])]
    assert dependents and all(o["status"] == "stale" for o in dependents)


def test_new_proposal_supersedes_open_one_and_apply_requires_accepted(env):
    repository.save(synthetic_doc(), updated_by="test", bump=False)
    op = mops.parse_op({"op": "set_column_alias", "field_id": "mrp_type", "side": "ecc", "column": "MRP Type"})
    p1 = proposals.create("TEST", [op], run_id=None, changeset_id=None, mode="incremental", summary="a")
    with pytest.raises(proposals.ProposalError):
        proposals.apply(p1["proposal_id"])
    op2 = mops.parse_op({"op": "set_column_alias", "field_id": "mrp_type", "side": "ecc", "column": "MRP Type"})
    proposals.create("TEST", [op2], run_id=None, changeset_id=None, mode="incremental", summary="b")
    assert proposals.get(p1["proposal_id"])["status"] == "superseded"
    with pytest.raises(proposals.ProposalError):
        proposals.decide(p1["proposal_id"], [{"op_id": "op001", "decision": "accepted"}])
