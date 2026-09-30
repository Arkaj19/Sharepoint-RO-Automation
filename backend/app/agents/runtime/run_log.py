"""
Audit trail of one agent run:

  data/agent_runs/<run_id>/run.json          summary (provider, model, prompt hash,
                                             steps, token usage, outcome, proposal)
  data/agent_runs/<run_id>/transcript.jsonl  every message sent to / received from
                                             the model and every tool call + result
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from app.core import paths
from app.core.jsonio import append_jsonl, read_json, read_jsonl, write_json_atomic


def new_run_id(obj: str) -> str:
    return f"RUN-{obj}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class RunLog:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.dir = paths.agent_runs_dir() / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.record: dict = {"run_id": run_id, "started_at": _now(), "status": "running"}
        self.save()

    def event(self, kind: str, **data) -> None:
        append_jsonl(self.dir / "transcript.jsonl", {"at": _now(), "kind": kind, **data})

    def update(self, **data) -> None:
        self.record.update(data)
        self.save()

    def finish(self, status: str, **data) -> None:
        self.record.update(data)
        self.record["status"] = status
        self.record["ended_at"] = _now()
        self.save()

    def save(self) -> None:
        write_json_atomic(self.dir / "run.json", self.record)


def get_run(run_id: str) -> dict | None:
    return read_json(paths.agent_runs_dir() / run_id / "run.json")


def get_transcript(run_id: str) -> list[dict]:
    return read_jsonl(paths.agent_runs_dir() / run_id / "transcript.jsonl")


def list_runs(obj: str | None = None, limit: int = 30) -> list[dict]:
    d = paths.agent_runs_dir()
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("RUN-*"), key=lambda p: p.stat().st_mtime, reverse=True):
        r = read_json(p / "run.json")
        if r and (not obj or r.get("object") == obj):
            out.append(r)
        if len(out) >= limit:
            break
    return out
