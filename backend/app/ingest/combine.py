"""
Concatenates the part-files of one dataset into a single frame, tagging
every row with the file it came from (__src_file) and that file's part
label (__src_part, e.g. the source plant of an S/4 per-plant extract).
"""
from __future__ import annotations

import pandas as pd

from app.ingest.readers import SRC_FILE_COL, SRC_PART_COL


def combine(parts: list[tuple[str, str, pd.DataFrame]]) -> pd.DataFrame:
    """parts = [(filename, part_label, frame), ...]"""
    frames = []
    for filename, label, df in parts:
        df = df.copy()
        df[SRC_FILE_COL] = filename
        df[SRC_PART_COL] = label
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    # parts with different column sets leave NaN in the gaps; keep text semantics
    return out.fillna("")
