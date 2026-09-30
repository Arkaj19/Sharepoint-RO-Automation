"""
Loads the supporting tables a mapping's lookups need, from the current
snapshot versions: a migration object's ECC extract (e.g. MARC for the MBEW
rules) or a supporting dataset (REF/MARA, REF/MARM, ...). A table that
hasn't been delivered comes back as None.
"""
from __future__ import annotations

import pandas as pd

from app.ingest.sources import OBJECTS, REF
from app.mapping.model import MappingDoc
from app.store import snapshot_store as ss


def dataset_frame(name: str) -> pd.DataFrame | None:
    try:
        if name in OBJECTS:
            return ss.read_combined(name, "ECC")
        return ss.read_combined(REF, name)
    except ss.SnapshotNotFound:
        return None


def load_refs(doc: MappingDoc) -> dict[str, pd.DataFrame | None]:
    return {lk.table: dataset_frame(lk.table) for lk in doc.lookups}


def availability(doc: MappingDoc) -> dict[str, bool]:
    return {lk.table: dataset_frame(lk.table) is not None for lk in doc.lookups}
