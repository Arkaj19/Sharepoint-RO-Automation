"""
A tiny cross-platform file lock, used so only one refresh runs at a time.
The lock file holds the owner's id; a stale lock (older than max_age) is
broken automatically so a crashed run can't block refreshes forever.
"""
from __future__ import annotations

import os
import time
from pathlib import Path


class LockBusy(RuntimeError):
    pass


class FileLock:
    def __init__(self, path: Path, owner: str, max_age_seconds: int = 6 * 3600) -> None:
        self.path = Path(path)
        self.owner = owner
        self.max_age = max_age_seconds

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > self.max_age:
            self.path.unlink(missing_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            holder = self.path.read_text(encoding="utf-8", errors="replace").strip()
            raise LockBusy(f"Another refresh is already running ({holder}).") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(self.owner)

    def release(self) -> None:
        try:
            if self.path.read_text(encoding="utf-8").strip() == self.owner:
                self.path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
