"""SOURCE_MODE=local: a folder that mirrors the SharePoint library."""
import os

import pandas as pd
import pytest

from app.connectors import get_client
from app.connectors.local_source import LocalSourceClient, LocalSourceError
from app.core.config import settings
from app.mapping import repository
from app.store import snapshot_store as ss
from app.workflows import refresh
from tests.unit.test_snapshots_and_refresh import _csv, _xlsx


@pytest.fixture
def local_lib(env, monkeypatch, synthetic):
    doc, ecc, s4 = synthetic
    doc.object = "MARC"
    repository.save(doc, updated_by="test", bump=False)
    root = env / "library"
    (root / settings.FOLDER_PATH).mkdir(parents=True)
    (root / "MARC_DAP_2026.XLSX").write_bytes(_xlsx(ecc))
    (root / "notes.docx").write_bytes(b"x")
    for p in ("US27", "US29", "US30"):
        (root / settings.FOLDER_PATH / f"S_MARC#FreeText - {p}.csv").write_bytes(_csv(s4[s4["werks"] == p]))
    monkeypatch.setattr(settings, "SOURCE_MODE", "local")
    monkeypatch.setattr(settings, "LOCAL_SOURCE_ROOT", str(root))
    monkeypatch.setattr(settings, "ECC_FOLDER_PATH", "")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "none")
    return root


def _run():
    rec = refresh.create(objects=["MARC"])
    return refresh.execute(rec["refresh_run_id"])


def test_factory_and_listing(local_lib):
    client = get_client()
    assert isinstance(client, LocalSourceClient)
    items = {i["name"]: i for i in client.list_folder_items(client.get_site_id(), "")}
    assert "file" in items["MARC_DAP_2026.XLSX"] and "folder" in items[settings.FOLDER_PATH]
    assert items["notes.docx"]["id"] == "localsrc:notes.docx"          # relative: a moved folder keeps its fingerprints
    assert client.download_file("local", items["notes.docx"]["id"]) == b"x"
    with pytest.raises(LocalSourceError):
        client.download_file("local", "localsrc:../outside.txt")


def test_local_refresh_detects_edits(local_lib):
    rec = _run()
    assert rec["status"] == "completed", rec.get("error")
    assert rec["datasets"]["MARC/ECC"]["status"] == "changed"

    assert _run()["status"] == "no_changes"

    part = local_lib / settings.FOLDER_PATH / "S_MARC#FreeText - US29.csv"
    df = pd.read_csv(part, dtype=str, keep_default_na=False)
    df.loc[0, "DISMM"] = "ZZ"
    part.write_bytes(_csv(df))
    st = part.stat()
    os.utime(part, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))   # distinct mtime even on coarse clocks
    rec3 = _run()
    assert rec3["datasets"]["MARC/S4"]["status"] == "changed"
    assert rec3["datasets"]["MARC/ECC"]["status"] == "unchanged"
    assert len(ss.versions("MARC", "S4")["previous"]) == 1


def test_copied_source_folder_keeps_fingerprints(local_lib, tmp_path, monkeypatch):
    import shutil
    first = _run()
    assert first["status"] == "completed"
    moved = tmp_path / "moved"
    shutil.copytree(local_lib, moved)
    monkeypatch.setattr(settings, "LOCAL_SOURCE_ROOT", str(moved))
    rec = _run()
    assert rec["status"] == "no_changes"
    assert rec["datasets"]["MARC/ECC"]["reason"] == "SharePoint fingerprint unchanged"


def test_local_mode_needs_a_root(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_SOURCE_ROOT", "")
    with pytest.raises(LocalSourceError):
        LocalSourceClient()


def test_forced_refresh_of_identical_files_keeps_the_current_version(local_lib):
    first = _run()
    assert first["status"] == "completed"
    vid = ss.versions("MARC", "ECC")["current"]
    rec = refresh.create(objects=["MARC"], force=True)
    rec = refresh.execute(rec["refresh_run_id"])
    assert rec["status"] == "completed"                               # still re-diffed and re-reviewed
    assert rec["datasets"]["MARC/ECC"]["status"] == "unchanged"
    assert "identical" in rec["datasets"]["MARC/ECC"]["reason"]
    assert ss.versions("MARC", "ECC") == {"current": vid, "previous": []}   # no duplicate version
    assert rec["changesets"]["MARC"]


def test_vanished_supporting_table_is_kept_with_a_warning(local_lib):
    (local_lib / "MARA_DAP 1.csv").write_text("Material,Material Type\n100000,FERT\n", encoding="utf-8")
    assert _run()["datasets"]["REF/MARA"]["status"] == "changed"
    (local_lib / "MARA_DAP 1.csv").unlink()
    rec = _run()
    assert rec["status"] == "no_changes"
    assert rec["datasets"]["REF/MARA"]["status"] == "kept"
    assert "stored version is kept" in rec["datasets"]["REF/MARA"]["reason"]
    assert rec["warnings"] and "REF/MARA" in rec["warnings"][0]
    assert "warning" in rec["messages"][-1]["message"]
    assert ss.current_manifest("REF", "MARA")                     # the stored copy is still used
