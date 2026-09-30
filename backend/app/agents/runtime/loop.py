"""
The plain tool-calling loop: send messages, run the tools the model asks
for, feed results back, stop when the finish tool is called or a limit is
hit (step cap, token budget). No framework - small enough to read in one go.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.agents.llm.gateway import ModelGateway, Usage
from app.agents.runtime.run_log import RunLog
from app.agents.runtime.tools import ToolRegistry

NUDGE = ("You have not called {finish} yet. Continue with tool calls, and call {finish} "
         "with your summary when the review is complete.")
WRAP_UP = ("You are close to this run's step/token limit. Stop investigating: record every change you have "
           "already verified (add_ops / drop_candidates / adjust_candidates), then call {finish} with your "
           "summary, including what you could not finish.")
FINAL = "Last step: call {finish} now with your summary."
MAX_NUDGES = 2
WRAP_UP_STEPS_LEFT = 5        # wrap-up starts with this many steps left ...
WRAP_UP_BUDGET_SHARE = 0.75   # ... or once this share of the token budget is used


@dataclass
class LoopResult:
    steps: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""
    final_text: str | None = None


def run_tool_loop(gateway: ModelGateway, *, system: str, user: str, registry: ToolRegistry,
                  is_finished: Callable[[], bool], finish_tool: str, max_steps: int, token_budget: int,
                  max_output_tokens: int, reasoning_effort: str | None, log: RunLog,
                  wrap_up_tools: set[str] | None = None) -> LoopResult:
    """Near the limits the model gets a wrap-up message and only `wrap_up_tools`
    (plus the finish tool), so a long review still ends with a submitted proposal."""
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    log.event("message", role="system", content=system)
    log.event("message", role="user", content=user)
    res = LoopResult()
    nudges = 0
    specs = registry.specs()
    keep = (wrap_up_tools or set()) | {finish_tool}
    wrapping_up = False
    while True:
        near_limit = (res.steps >= max_steps - WRAP_UP_STEPS_LEFT
                      or res.usage.total_tokens >= token_budget * WRAP_UP_BUDGET_SHARE)
        if near_limit and not wrapping_up:
            wrapping_up = True
            specs = [t for t in specs if t.name in keep]
            messages.append({"role": "user", "content": WRAP_UP.format(finish=finish_tool)})
            log.event("message", role="user", content="[wrap-up] " + WRAP_UP.format(finish=finish_tool))
        if wrapping_up and res.steps == max_steps - 1 and not is_finished():
            # the last step is reserved for submitting
            specs = [t for t in specs if t.name == finish_tool]
            messages.append({"role": "user", "content": FINAL.format(finish=finish_tool)})
        if res.steps >= max_steps:
            res.stop_reason = "max_steps"
            break
        if res.usage.total_tokens >= token_budget:
            res.stop_reason = "token_budget"
            break
        turn = gateway.complete(messages, specs, max_output_tokens=max_output_tokens,
                                reasoning_effort=reasoning_effort)
        res.steps += 1
        res.usage.add(turn.usage)
        messages.append(turn.assistant_message())
        log.event("model_turn", step=res.steps, text=turn.text, finish_reason=turn.finish_reason,
                  tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in turn.tool_calls],
                  usage=turn.usage.__dict__)
        if not turn.tool_calls:
            res.final_text = turn.text
            if is_finished():
                res.stop_reason = "finished"
                break
            if nudges >= MAX_NUDGES:
                res.stop_reason = "no_finish"
                break
            nudges += 1
            messages.append({"role": "user", "content": NUDGE.format(finish=finish_tool)})
            continue
        for call in turn.tool_calls:
            text, ok = registry.dispatch(call.name, call.arguments)
            res.tool_calls += 1
            res.tool_errors += 0 if ok else 1
            messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
            log.event("tool_result", step=res.steps, id=call.id, name=call.name, ok=ok, content=text)
        if is_finished():
            res.stop_reason = "finished"
            break
    return res
