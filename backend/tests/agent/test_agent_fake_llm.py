"""Agent 1 with a scripted model: tool dispatch, self-correction, fallbacks."""
import json

from app.agents.llm.fake import FakeGateway
from app.agents.rulebook_agent import agent
from app.agents.runtime import run_log
from app.core.config import settings
from app.diff import changeset
from app.mapping import repository
from app.proposals import service as proposals
from tests.conftest import put_snapshot


def _setup(synthetic):
    doc, ecc, s4 = synthetic
    repository.save(doc, updated_by="test", bump=False)
    put_snapshot("TEST", "ECC", ecc)
    put_snapshot("TEST", "S4", s4)
    return changeset.build("TEST")


def _tool_results(messages):
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def test_model_reviews_drops_adds_and_submits(env, synthetic):
    cs = _setup(synthetic)
    state = {"turn": 0}

    def script(messages):
        state["turn"] += 1
        t = state["turn"]
        if t == 1:
            return {"tool_calls": [{"name": "list_candidates", "arguments": {"op": "ignore_column"}}]}
        if t == 2:
            ignore_id = _tool_results(messages)[-1]["items"][0]["id"]
            return {"tool_calls": [
                {"name": "drop_candidates", "arguments": {"ids": [ignore_id], "reason": "EXTRA is needed later"}},
                {"name": "add_ops", "arguments": {"ops": [
                    {"op": "set_column_alias", "field_id": "mrp_type", "side": "ecc", "column": "Nope"}]}},
            ]}
        if t == 3:
            rejected = _tool_results(messages)[-1]["rejected"]
            assert "does not exist" in rejected[0]["errors"][0]      # the model sees the validation error
            return {"tool_calls": [{"name": "add_ops", "arguments": {"ops": [
                {"op": "update_field_attrs", "field_id": "mrp_type", "set": {"length": 4},
                 "reason": "S/4 MRP type is CHAR 4", "confidence": 0.8},
                {"op": "set_column_alias", "field_id": "mrp_type", "side": "ecc", "column": "MRP Type"}]}}]}
        if t == 4:
            dup = _tool_results(messages)[-1]["duplicates"]
            assert dup and dup[0]["op"] == "set_column_alias"         # already a candidate
            return {"tool_calls": [{"name": "submit_proposal", "arguments": {"summary": "Reviewed. Dropped EXTRA."}}]}
        return {"text": "done"}

    rec = agent.run("TEST", cs["changeset_id"], gateway=FakeGateway(script))
    assert rec["status"] == "completed"
    assert rec["loop"]["stop_reason"] == "finished"
    p = proposals.get(rec["proposal_id"])
    assert p["summary"].startswith("Reviewed.")
    assert not any(o["op"] == "ignore_column" for o in p["ops"])
    added = [o for o in p["ops"] if o["source"] == "llm"]
    assert len(added) == 1 and added[0]["op"] == "update_field_attrs"
    assert any(d["by"] == "llm" for d in p["dropped_ops"])
    kinds = [e["kind"] for e in run_log.get_transcript(rec["run_id"])]
    assert "model_turn" in kinds and "tool_result" in kinds


def test_model_failure_falls_back_to_deterministic_proposal(env, synthetic):
    cs = _setup(synthetic)

    def broken(messages):
        raise RuntimeError("endpoint down")

    rec = agent.run("TEST", cs["changeset_id"], gateway=FakeGateway(broken))
    p = proposals.get(rec["proposal_id"])
    assert rec["status"] == "completed"
    assert "LLM review failed" in p["summary"]
    assert len(p["ops"]) > 5


def test_step_cap_stops_the_loop_and_keeps_candidates(env, synthetic, monkeypatch):
    cs = _setup(synthetic)
    monkeypatch.setattr(settings, "LLM_MAX_STEPS", 2)
    loop = [{"tool_calls": [{"name": "get_mapping_summary", "arguments": {}}]}] * 5
    rec = agent.run("TEST", cs["changeset_id"], gateway=FakeGateway(loop))
    assert rec["loop"]["stop_reason"] == "max_steps"
    assert "stopped before submitting" in proposals.get(rec["proposal_id"])["summary"]


def test_wrap_up_restricts_tools_and_ends_with_submit(env, synthetic, monkeypatch):
    cs = _setup(synthetic)
    monkeypatch.setattr(settings, "LLM_MAX_STEPS", 8)     # wrap-up starts 5 steps before the cap
    seen_tools = []

    def script(messages):
        wrap = any("close to this run's step/token limit" in (m.get("content") or "") for m in messages
                   if m["role"] == "user")
        if not wrap:
            return {"tool_calls": [{"name": "get_mapping_summary", "arguments": {}}]}
        return {"tool_calls": [{"name": "submit_proposal", "arguments": {"summary": "Wrapped up."}}]}

    gw = FakeGateway(script)
    original = gw.complete

    def spy(messages, tools, **kw):
        seen_tools.append({t.name for t in tools})
        return original(messages, tools, **kw)

    gw.complete = spy
    rec = agent.run("TEST", cs["changeset_id"], gateway=gw)
    assert rec["loop"]["stop_reason"] == "finished"
    assert proposals.get(rec["proposal_id"])["summary"].startswith("Wrapped up.")
    assert "get_mapping_summary" in seen_tools[0]
    assert {"add_ops", "drop_candidates", "adjust_candidates", "validate_ops", "submit_proposal"} in seen_tools


def test_last_step_is_reserved_for_submit(env, synthetic, monkeypatch):
    cs = _setup(synthetic)
    monkeypatch.setattr(settings, "LLM_MAX_STEPS", 6)
    seen_tools = []

    def script(messages):
        if any("Last step" in (m.get("content") or "") for m in messages if m["role"] == "user"):
            return {"tool_calls": [{"name": "submit_proposal", "arguments": {"summary": "Final."}}]}
        return {"tool_calls": [{"name": "adjust_candidates", "arguments": {"updates": []}}]}

    gw = FakeGateway(script)
    original = gw.complete
    gw.complete = lambda m, tools, **kw: (seen_tools.append({t.name for t in tools}), original(m, tools, **kw))[1]
    rec = agent.run("TEST", cs["changeset_id"], gateway=gw)
    assert rec["loop"]["stop_reason"] == "finished" and rec["loop"]["steps"] == 6
    assert seen_tools[-1] == {"submit_proposal"}


def test_no_provider_is_deterministic_only(env, synthetic):
    cs = _setup(synthetic)
    rec = agent.run("TEST", cs["changeset_id"], provider="none")
    assert rec["provider"] == "none"
    assert proposals.get(rec["proposal_id"])["ops"]
