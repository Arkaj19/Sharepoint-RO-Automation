"""
Imports the combined CSVs written by the old fetch routes
(data/combined/<OBJECT>/<SIDE>/<OBJECT>_<SIDE>_combined.csv) as the first
snapshot version of each dataset, so the diff and Agent 1 have a baseline
without touching SharePoint.

  python -m scripts.import_legacy_combined [--force]

Datasets that already have a current version are skipped unless --force.

Only for offline work (no SharePoint access). The legacy files are lossy: the
old fetch parsed numbers before writing them ('0.000' became '0.0', 'false'
became 'False'), and they carry no lineage columns (__src_file / __src_part).
Diffs against a real SharePoint version will therefore flag every numeric
cell. When SharePoint is reachable, let the first "Refresh data" be the
baseline instead.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core import paths  # noqa: E402
from app.ingest.readers import read_table  # noqa: E402
from app.ingest.sources import SOURCES  # noqa: E402
from app.profiling.profiler import profile_frame  # noqa: E402
from app.store import snapshot_store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    root = paths.legacy_combined_dir()
    for spec in SOURCES:
        src = root / spec.object / spec.side / f"{spec.object}_{spec.side}_combined.csv"
        if not src.exists():
            print(f"skip {spec.dataset}: {src} not found")
            continue
        if snapshot_store.current_manifest(spec.object, spec.side) and not args.force:
            print(f"skip {spec.dataset}: already has a current version")
            continue
        df = read_table(str(src))
        vid, staging = snapshot_store.begin(spec.object, spec.side)
        snapshot_store.commit(
            spec.object, spec.side, vid, staging,
            {
                "refresh_run_id": None,
                "source": {"kind": "legacy_import", "path": str(src)},
                "listing_fingerprint": None,
                "files": [],
            },
            df, profile_frame(df),
        )
        print(f"imported {spec.dataset} as {vid}: {len(df)} rows x {len(df.columns)} cols")


if __name__ == "__main__":
    main()
