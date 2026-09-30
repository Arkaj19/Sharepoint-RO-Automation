"""
End to end on the real extracts (the baseline snapshots imported from the
old combined CSVs). Skipped when backend/data/snapshots is missing.

  bootstrap proposal -> accept confident ops -> apply -> validation now
  aligns keys and compares values (instead of reporting every field missing).
"""
import shutil
from pathlib import Path

import pytest

from app.agents.rulebook_agent import agent
from app.diff import changeset
from app.mapping import repository
from app.proposals import service as proposals
from app.store import snapshot_store as ss
from app.validation.service import validate

BACKEND = Path(__file__).resolve().parents[2]
REAL_SNAPSHOTS = BACKEND / "data" / "snapshots"
REAL_MAPPINGS = BACKEND / "mappings"

pytestmark = pytest.mark.skipif(not (REAL_SNAPSHOTS / "MBEW").exists(), reason="no baseline snapshots")


def test_mbew_bootstrap_to_validation(env):
    shutil.copytree(REAL_SNAPSHOTS / "MBEW", env / "data" / "snapshots" / "MBEW")
    shutil.copy(REAL_MAPPINGS / "MBEW.yaml", env / "mappings" / "MBEW.yaml")
    repository._cache.clear()

    cs = changeset.build("MBEW")
    assert cs["alignment"]["stats"]["s4_rows_matched_share"] > 0.95
    rec = agent.run("MBEW", cs["changeset_id"], provider="none")
    p = proposals.get(rec["proposal_id"])
    xw = next(o for o in p["ops"] if o["op"] == "add_value_map_entries" and o["map_id"].endswith("_crosswalk"))
    pairs = {(e["from"], e["to"]) for e in xw["entries"]}
    assert {("1025", "US29"), ("1029", "CA02"), ("1021", "US27"), ("1021", "US30")} <= pairs

    proposals.decide(p["proposal_id"], [{"op_id": o["op_id"], "decision": "accepted" if o["confidence"] >= 0.85
                                         else "rejected"} for o in p["ops"]], by="e2e")
    p = proposals.apply(p["proposal_id"], by="e2e")
    assert not [o for o in p["ops"] if o["status"] == "failed"]

    report = validate("MBEW", ss.read_combined("MBEW", "ECC"), ss.read_combined("MBEW", "S4"))
    by = {r.rule_id: r for r in report.rules}
    assert by["key_integrity"].status.value in ("pass", "fail")          # it ran - keys resolved
    assert "keys match" in by["key_integrity"].summary
    matched = int(by["key_integrity"].summary.split(";")[-1].split()[0])
    assert matched > 20000
    assert by["value_transformation"].status.value in ("pass", "fail")
