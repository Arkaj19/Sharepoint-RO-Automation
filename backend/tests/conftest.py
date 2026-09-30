"""
Shared fixtures.

`env` points DATA_ROOT and MAPPINGS_DIR at a temporary folder, so no test
touches backend/data or backend/mappings.

`synthetic` builds a small ECC/S4 pair that has the real data's traits:
  * ECC material un-padded, S/4 material zero-padded to 18
  * ECC screen-label headers, S/4 technical headers
  * plant crosswalk with a fan-out: 1021 -> US27 + US30, 1025 -> US29
  * in US30, procurement type E becomes F (a split on the target plant)
  * S/4 numbers written as '12.0'
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import paths  # noqa: E402
from app.mapping import repository  # noqa: E402
from app.mapping.model import EccSource, FieldSpec, KeySpec, MappingDoc  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(paths, "MAPPINGS_DIR", tmp_path / "mappings")
    repository._cache.clear()
    (tmp_path / "mappings").mkdir()
    return tmp_path


def synthetic_doc(obj: str = "TEST") -> MappingDoc:
    def f(fid, label, s4, dtype="Text", length=40, mandatory=False, group=None):
        return FieldSpec(id=fid, s4_label=label, s4_column=s4, data_type=dtype, length=length,
                         mandatory=mandatory, group=group, ecc_source=EccSource(table="MARC", field=s4))
    return MappingDoc(
        object=obj, s4_sheet=f"S_{obj}", key=KeySpec(fields=["product_number", "plant"]),
        fields=[
            f("product_number", "Product Number", "PRODUCT", mandatory=True, group="Key"),
            f("plant", "Plant", "werks", mandatory=True),
            f("mrp_type", "MRP Type", "DISMM"),
            f("procurement_type", "Procurement Type", "BESKZ"),
            f("safety_stock", "Safety Stock", "EISBE", dtype="Number", length=13),
        ],
    )


def synthetic_frames(n_materials: int = 60, extra_ecc_plant: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    ecc_rows, s4_rows = [], []
    for i in range(n_materials):
        mat = str(100000 + i)
        proc = "E" if i % 3 == 0 else "F"
        mrp = ["PD", "VB", "ND"][i % 3]
        stock = str(i * 5)
        plants = ["1021"] if i % 2 == 0 else ["1025"]
        if i % 5 == 0:
            plants.append("1025" if plants[0] == "1021" else "1021")
        if extra_ecc_plant and i % 7 == 0:
            plants.append("1099")                 # not migrated: no S/4 counterpart
        for p in plants:
            ecc_rows.append({"Material": mat, "Plant": p, "MRP Type": mrp, "Procurement type": proc,
                             "Safety Stock": stock, "Comment": f"row {i}"})
            targets = {"1021": ["US27", "US30"], "1025": ["US29"]}.get(p, [])
            for t in targets:
                s4_proc = "F" if (t == "US30" and proc == "E") else proc
                s4_rows.append({"PRODUCT": mat.zfill(18), "werks": t, "DISMM": mrp, "BESKZ": s4_proc,
                                "EISBE": f"{int(stock)}.0", "EXTRA": ""})
    return pd.DataFrame(ecc_rows, dtype=str), pd.DataFrame(s4_rows, dtype=str)


@pytest.fixture
def synthetic():
    ecc, s4 = synthetic_frames()
    return synthetic_doc(), ecc, s4


def put_snapshot(obj: str, side: str, df: pd.DataFrame, files: list[dict] | None = None) -> str:
    from app.profiling.profiler import profile_frame
    from app.store import snapshot_store as ss
    vid, staging = ss.begin(obj, side)
    ss.commit(obj, side, vid, staging, {"source": {"kind": "test"}, "listing_fingerprint": None,
                                        "files": files or []}, df, profile_frame(df))
    return vid
