import io

import pandas as pd
import pytest

from app.core.config import settings
from app.store import snapshot_store as ss
from app.workflows import refresh
from tests.conftest import put_snapshot


def test_retention_keeps_current_plus_two_and_archives_the_rest(env, monkeypatch):
    monkeypatch.setattr(settings, "SNAPSHOT_KEEP_PREVIOUS", 2)
    vids = [put_snapshot("OBJ", "S4", pd.DataFrame({"a": [str(i)]})) for i in range(4)]
    v = ss.versions("OBJ", "S4")
    assert v["current"] == vids[3]
    assert v["previous"] == [vids[2], vids[1]]
    assert ss.archived("OBJ", "S4") == [vids[0]]
    assert not ss.version_dir("OBJ", "S4", vids[0]).exists()
    assert ss.read_combined("OBJ", "S4", "previous")["a"].tolist() == ["2"]
    with pytest.raises(ss.SnapshotNotFound):
        ss.resolve_version("OBJ", "S4", vids[0])


def test_listing_fingerprint_ignores_etag_only_changes():
    a = [{"name": "f.csv", "id": "1", "cTag": "c1", "eTag": "e1", "size": 10}]
    b = [{"name": "f.csv", "id": "1", "cTag": "c1", "eTag": "e2", "size": 10}]
    c = [{"name": "f.csv", "id": "1", "cTag": "c2", "eTag": "e2", "size": 10}]
    assert ss.listing_fingerprint(a) == ss.listing_fingerprint(b) != ss.listing_fingerprint(c)


def _csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def _xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


class FakeSharePoint:
    """In-memory stand-in for the Graph client: folder -> {name: bytes}."""

    def __init__(self, folders: dict[str, dict[str, bytes]]):
        self.folders = folders
        self.downloads = 0

    def get_site_id(self):
        return "site"

    def list_folder_items(self, site_id, folder):
        items = []
        for name, content in self.folders.get(folder, {}).items():
            items.append({"id": f"{folder}/{name}", "name": name, "size": len(content),
                          "cTag": str(hash(content)), "file": {"hashes": {}}})
        return items

    def download_file(self, site_id, item_id):
        self.downloads += 1
        folder, name = item_id.split("/", 1)
        return self.folders[folder][name]


@pytest.fixture
def fake_sp(env, monkeypatch, synthetic):
    doc, ecc, s4 = synthetic
    from app.mapping import repository
    doc.object = "MARC"
    repository.save(doc, updated_by="test", bump=False)
    s4_parts = {f"S_MARC#FreeText - {p}.csv": _csv(s4[s4["werks"] == p]) for p in ("US27", "US29", "US30")}
    sp = FakeSharePoint({
        "": {"MARC_DAP_2026.XLSX": _xlsx(ecc), "unrelated.docx": b"x"},
        settings.FOLDER_PATH: s4_parts,
    })
    monkeypatch.setattr(refresh, "get_client", lambda: sp)
    monkeypatch.setattr(settings, "ECC_FOLDER_PATH", "")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "none")
    return sp


def _run(**kw):
    rec = refresh.create(objects=["MARC"], **kw)
    return refresh.execute(rec["refresh_run_id"])


def test_refresh_stores_versions_diffs_and_proposes(fake_sp):
    rec = _run()
    assert rec["status"] == "completed", rec.get("error")
    assert rec["datasets"]["MARC/S4"]["status"] == "changed"
    m = ss.manifest("MARC", "S4")
    assert [f["part_label"] for f in m["files"]] == ["US27", "US29", "US30"]
    assert "__src_part" in ss.read_combined("MARC", "S4").columns
    assert rec["agent_runs"]["MARC"]["proposal_id"]

    # same files again -> nothing downloaded, no new version, no agent run
    downloads = fake_sp.downloads
    rec2 = _run()
    assert rec2["status"] == "no_changes"
    assert fake_sp.downloads == downloads
    assert ss.versions("MARC", "S4")["previous"] == []

    # change one S/4 file -> only S4 gets a new version; the ECC side stays
    folder = fake_sp.folders[settings.FOLDER_PATH]
    name = "S_MARC#FreeText - US29.csv"
    df = pd.read_csv(io.BytesIO(folder[name]), dtype=str, keep_default_na=False)
    df.loc[0, "DISMM"] = "ZZ"
    folder[name] = _csv(df)
    rec3 = _run()
    assert rec3["datasets"]["MARC/S4"]["status"] == "changed"
    assert rec3["datasets"]["MARC/ECC"]["status"] == "unchanged"
    assert len(ss.versions("MARC", "S4")["previous"]) == 1


def test_missing_files_keep_current_version(fake_sp):
    _run()
    cur = ss.versions("MARC", "S4")["current"]
    fake_sp.folders[settings.FOLDER_PATH] = {}
    rec = _run()
    assert rec["datasets"]["MARC/S4"]["status"] == "error"
    assert ss.versions("MARC", "S4")["current"] == cur
