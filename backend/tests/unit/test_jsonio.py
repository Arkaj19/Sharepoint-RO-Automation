"""Atomic JSON writes survive a concurrent reader (Windows sharing violations)."""
import threading
import time

from app.core.jsonio import read_json, write_json_atomic


def test_write_waits_for_a_reader_that_holds_the_file(tmp_path):
    p = tmp_path / "run.json"
    write_json_atomic(p, {"status": "running"})
    fh = open(p, "r", encoding="utf-8")            # a poller has the file open
    threading.Timer(0.3, fh.close).start()         # ... and closes it shortly after
    write_json_atomic(p, {"status": "completed"})  # must not raise PermissionError on Windows
    assert read_json(p) == {"status": "completed"}


def test_reads_during_many_rewrites_never_fail(tmp_path):
    p = tmp_path / "run.json"
    write_json_atomic(p, {"n": 0})
    errors, stop = [], time.time() + 1.0

    def reader():
        while time.time() < stop:
            try:
                assert "n" in read_json(p)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
    t = threading.Thread(target=reader)
    t.start()
    n = 0
    while time.time() < stop:
        n += 1
        write_json_atomic(p, {"n": n})
    t.join()
    assert not errors, errors[:3]
    assert read_json(p)["n"] == n
