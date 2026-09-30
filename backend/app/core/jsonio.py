"""
JSON helpers. Writes are atomic (temp file + os.replace) so a crash never
leaves a half-written manifest, proposal or run record behind.

On Windows a file that another thread or process has open cannot be
replaced (and cannot be opened while it is being replaced): the UI polls run
records while the background job rewrites them. Both sides therefore retry
briefly on PermissionError instead of failing the job or the request.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

_RETRY_DELAYS = (0.01, 0.02, 0.05, 0.1, 0.1, 0.2, 0.2, 0.3, 0.5, 0.5)   # ~2 s in total


def _retry(fn):
    for delay in _RETRY_DELAYS:
        try:
            return fn()
        except PermissionError:
            time.sleep(delay)
    return fn()


def read_json(path: Path | str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default

    def _read():
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    try:
        return _retry(_read)
    except FileNotFoundError:
        return default


def write_text_atomic(path: Path | str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        _retry(lambda: os.replace(tmp, p))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_json_atomic(path: Path | str, data: Any) -> None:
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False, default=str))


def append_jsonl(path: Path | str, record: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
