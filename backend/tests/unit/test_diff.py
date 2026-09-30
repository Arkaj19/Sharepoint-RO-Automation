import pandas as pd

from app.diff import changeset
from app.diff import datasets as dd
from app.mapping import repository
from app.profiling.profiler import profile_frame
from tests.conftest import put_snapshot


def test_schema_diff_detects_rename_add_remove():
    prev = pd.DataFrame({"MRP Type": ["PD", "VB"] * 10, "Plant": ["1021"] * 20, "Old": ["x"] * 20})
    cur = pd.DataFrame({"MRP type": ["PD", "VB"] * 10, "Plant": ["1021"] * 20, "New": ["1"] * 20})
    ch = dd.diff_schema(profile_frame(prev), profile_frame(cur), "ECC", dd._Ids("C"))
    kinds = {(c["kind"], c.get("from"), c.get("to"), c.get("column")) for c in ch}
    assert ("column_renamed", "MRP Type", "MRP type", None) in kinds
    assert any(c["kind"] == "column_removed" and c["column"] == "Old" for c in ch)
    assert any(c["kind"] == "column_added" and c["column"] == "New" for c in ch)


def test_profile_and_key_diff():
    prev = pd.DataFrame({"Material": ["1", "2", "3"], "Plant": ["1021", "1021", "1025"], "Stock": ["5", "6", "7"]})
    cur = pd.DataFrame({"Material": ["1", "2", "4"], "Plant": ["1021", "1044", "1025"], "Stock": ["5", "9", "7"]})
    ids = dd._Ids("C")
    prof = dd.diff_profiles(profile_frame(prev), profile_frame(cur), "ECC", ids)
    new_codes = next(c for c in prof if c["kind"] == "new_code_values" and c["column"] == "Plant")
    assert new_codes["values"] == {"1044": 1}
    keys = dd.diff_keys(prev, cur, ["Material"], [None], "ECC", ids)
    assert next(c for c in keys if c["kind"] == "keys_added")["samples"] == ["4"]
    cells = next(c for c in keys if c["kind"] == "cells_changed")
    changed = {c["column"]: c["changed"] for c in cells["columns"]}
    assert changed == {"Plant": 1, "Stock": 1}


def test_changeset_tags_mapping_impact(env, synthetic):
    doc, ecc, s4 = synthetic
    doc.field("mrp_type").ecc_column = "MRP Type"
    repository.save(doc, updated_by="test", bump=False)
    put_snapshot("TEST", "ECC", ecc)
    put_snapshot("TEST", "S4", s4)
    cs0 = changeset.build("TEST")
    assert cs0["baseline"] is True and cs0["changes"] == []

    ecc2 = ecc.rename(columns={"MRP Type": "MRP type (new)"})
    put_snapshot("TEST", "ECC", ecc2)
    cs = changeset.build("TEST", dataset_status={"ECC": "changed", "S4": "unchanged"})
    assert cs["baseline"] is False
    renamed = [c for c in cs["changes"] if c["kind"] == "column_renamed"]
    assert renamed and renamed[0]["impacts"] == ["field:mrp_type"]
    assert {"field": "mrp_type", "side": "ECC", "column": "MRP Type"} in cs["mapping_health"]["broken_columns"]
    assert len(changeset.for_llm(cs, max_chars=4000)) <= 6000
