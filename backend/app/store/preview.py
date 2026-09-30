"""
Preview and download of a stored snapshot version (the combined data).
.xlsx is built in memory on demand; nothing extra is written to disk.
"""
from __future__ import annotations

import io

from app.store import snapshot_store as ss


def get_preview(obj: str, side: str, vid: str | None = None, limit: int = 20) -> dict:
    df = ss.read_combined(obj, side, vid)
    head = df.head(limit)
    return {
        "name": f"{obj}_{side}",
        "version_id": ss.resolve_version(obj, side, vid),
        "columns": [str(c) for c in df.columns],
        "rows": head.to_dict(orient="records"),
        "total_rows": int(len(df)),
        "preview_row_count": int(len(head)),
    }


def build_xlsx_bytes(obj: str, side: str, vid: str | None = None) -> tuple[io.BytesIO, str]:
    vid = ss.resolve_version(obj, side, vid)
    df = ss.read_combined(obj, side, vid)
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)
    return buffer, f"{obj}_{side}_{vid}.xlsx"
