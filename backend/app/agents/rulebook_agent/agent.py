"""
Agent 1 - Rule Book agent. Entry point: run(object).

  1. Load the mapping, the latest ChangeSet and the current snapshots.
  2. Deterministic candidates (candidates.py) from measured evidence.
  3. If a model is configured, the model reviews them with tools
     (tools.py): verifies, drops, adjusts, adds, then submits a summary.
     Any model failure falls back to the deterministic proposal - a
     refresh never loses its proposal because the LLM was unavailable.
  4. Every operation is validated once more; invalid ones are dropped and
     logged. The result is stored as one proposal for human review.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.agents.llm.gateway import GatewayNotConfigured, ModelGateway, get_gateway
from app.agents.rulebook_agent import candidates
from app.agents.rulebook_agent.tools import AgentState, _op_brief, build_registry
from app.agents.runtime.loop import run_tool_loop
from app.agents.runtime.masking import Masker
from app.agents.runtime.run_log import RunLog, new_run_id
from app.core.config import settings
from app.diff import changeset as cs_mod
from app.mapping import ops as mops
from app.mapping import repository
from app.proposals import service as proposals
from app.store import snapshot_store as ss

PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"
FINISH_TOOL = "submit_proposal"


def _system_prompt() -> tuple[str, str]:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    return text, "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _user_message(st: AgentState) -> str:
    low = [_op_brief(o) for o in st.ops if o.confidence < 0.9 or o.gate == "G2"][:60]
    unresolved_counts: dict[str, int] = {}
    for u in st.unresolved:
        unresolved_counts[u["kind"]] = unresolved_counts.get(u["kind"], 0) + 1
    return "\n\n".join([
        f"Object: {st.doc.object} (mapping v{st.doc.mapping_version}, {len(st.doc.fields)} fields, "
        f"key {st.doc.key.fields}).",
        "ChangeSet (compact JSON):\n" + cs_mod.for_llm(st.changeset, max_chars=10000),
        f"Draft proposal: {len(st.ops)} candidate operations from the deterministic proposer. "
        f"{sum(1 for o in st.ops if o.confidence >= 0.9 and o.gate == 'G1')} high-confidence G1 candidates "
        f"are not listed below; use list_candidates to see them.",
        "Candidates to review (confidence < 0.9, or rules):\n" + json.dumps(low, ensure_ascii=False, default=str),
        "Unresolved items by kind: " + json.dumps(unresolved_counts) + " (use list_unresolved).",
        "Review, improve, then call submit_proposal.",
    ])


def _validate_all(st: AgentState) -> None:
    """Final gate: keep only ops that validate in sequence against the mapping."""
    results = mops.validate_ops(st.doc, st.ops, st.headers())
    keep = []
    bad_ids = set()
    for o, r in zip(st.ops, results):
        if r["ok"] and not set(o.depends_on) & bad_ids:
            if r["warnings"]:
                o.result = "; ".join(r["warnings"])
            keep.append(o)
        else:
            bad_ids.add(o.op_id)
            st.dropped.append({"op": mops.dump_op(o), "by": "validation",
                               "reason": "; ".join(r["errors"]) or "depends on a dropped op"})
    st.ops = keep


def run(obj: str, changeset_id: str | None = None, *, provider: str | None = None,
        gateway: ModelGateway | None = None, refresh_run_id: str | None = None) -> dict:
    run_id = new_run_id(obj)
    log = RunLog(run_id)
    system, prompt_hash = _system_prompt()
    doc = repository.load(obj)
    cs = cs_mod.load(changeset_id) if changeset_id else (cs_mod.latest(obj) or cs_mod.build(obj))
    mode = "bootstrap" if cs["baseline"] else "incremental"
    log.update(object=obj, mode=mode, changeset_id=cs["changeset_id"], refresh_run_id=refresh_run_id,
               mapping_version=doc.mapping_version, prompt_hash=prompt_hash,
               snapshots={side: ss.versions(obj, side)["current"] for side in ("ECC", "S4")})

    ecc, s4 = ss.read_combined(obj, "ECC"), ss.read_combined(obj, "S4")
    cand = candidates.build(doc, ecc, s4, cs)
    st = AgentState(doc=doc, changeset=cs, ecc=ecc, s4=s4, alignment=cand.alignment, ops=list(cand.ops),
                    unresolved=cand.unresolved)
    _validate_all(st)
    log.event("candidates", count=len(st.ops), unresolved=len(st.unresolved), notes=cand.notes,
              summary=candidates.summarize(cand))

    notes: list[str] = []
    loop_info: dict = {}
    if gateway is None:
        try:
            gateway = get_gateway(provider)
        except GatewayNotConfigured as exc:
            notes.append(f"LLM not used: {exc}")
            gateway = None
    if gateway is not None:
        code_cols = {c for c in list(ecc.columns) + list(s4.columns)
                     if 0 < ecc.get(c, s4.get(c)).nunique() <= 50}
        st.masker = Masker(settings.LLM_MASKING, code_cols)
        log.update(provider=gateway.provider, model=gateway.model)
        try:
            res = run_tool_loop(
                gateway, system=system, user=_user_message(st), registry=build_registry(st),
                is_finished=lambda: st.finished, finish_tool=FINISH_TOOL, max_steps=settings.LLM_MAX_STEPS,
                token_budget=settings.LLM_RUN_TOKEN_BUDGET, max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
                reasoning_effort=settings.LLM_REASONING_EFFORT or None, log=log,
                wrap_up_tools={"add_ops", "drop_candidates", "adjust_candidates", "validate_ops"})
            loop_info = {"steps": res.steps, "tool_calls": res.tool_calls, "tool_errors": res.tool_errors,
                         "usage": res.usage.__dict__, "stop_reason": res.stop_reason}
            if not st.finished:
                notes.append(f"The model stopped before submitting ({res.stop_reason}); "
                             f"its edits so far are kept.")
        except Exception as exc:  # noqa: BLE001 - never lose the deterministic proposal
            log.event("error", error=f"{type(exc).__name__}: {exc}")
            notes.append(f"LLM review failed ({type(exc).__name__}: {str(exc)[:300]}); "
                         f"the proposal contains the deterministic candidates only.")
            loop_info = {"error": f"{type(exc).__name__}: {exc}"}
    else:
        log.update(provider="none", model=None)

    _validate_all(st)
    summary = st.summary or candidates.summarize(
        candidates.CandidateSet(ops=st.ops, unresolved=st.unresolved))
    if notes:
        summary = summary + "\n\n" + "\n".join(notes)
    proposal = proposals.create(obj, st.ops, run_id=run_id, changeset_id=cs["changeset_id"], mode=mode,
                                summary=summary, dropped=st.dropped, base_mapping_version=doc.mapping_version)
    proposal["unresolved"] = st.unresolved
    proposals._save(proposal)
    log.finish("completed", proposal_id=proposal["proposal_id"], operations=len(st.ops),
               dropped=len(st.dropped), unresolved=len(st.unresolved), sample_rows_used=st.sample_rows_used,
               loop=loop_info, notes=notes)
    return log.record
