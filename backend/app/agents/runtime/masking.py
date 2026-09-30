"""
Optional masking of sample rows before they are sent to the model
(LLM_MASKING=true). Off by default - the current data policy allows
unmasked samples.

When on: low-cardinality code columns (plant, MRP type, ...) stay verbatim
because the agent needs them to reason about crosswalks; every other value
is replaced by a stable pseudonym with the same length and character
classes, so joins and patterns still look consistent within a run.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_DIGITS = "0123456789"
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class Masker:
    def __init__(self, enabled: bool, code_columns: set[str] | None = None) -> None:
        self.enabled = enabled
        self.code_columns = code_columns or set()
        self._key = secrets.token_bytes(16)

    def value(self, v: str) -> str:
        if not v:
            return v
        digest = hmac.new(self._key, v.encode("utf-8"), hashlib.sha256).digest()
        out = []
        for i, ch in enumerate(v):
            b = digest[i % len(digest)]
            if ch.isdigit():
                out.append(_DIGITS[b % 10])
            elif ch.isalpha():
                c = _LETTERS[b % 26]
                out.append(c if ch.isupper() else c.lower())
            else:
                out.append(ch)
        return "".join(out)

    def rows(self, rows: list[dict]) -> list[dict]:
        if not self.enabled:
            return rows
        return [{k: (v if k in self.code_columns else self.value(str(v))) for k, v in r.items()} for r in rows]
