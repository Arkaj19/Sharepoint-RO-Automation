"""
Fetches the raw SharePoint extracts (S_MARC#FreeText - *.csv,
S_MBEW#FreeText - *.csv, ...) and combines each group into one file.

Add a new row to GROUPS to support another file family later without
touching the API layer at all.
"""
from __future__ import annotations

import io
import os

import pandas as pd

from app.core.config import settings
from app.services.sharepoint_service import SharePointClient

# output file name (no extension)  ->  filename prefix to match in SharePoint
GROUPS: dict[str, str] = {
    "MARC_combined": "S_MARC#FreeText",
    "MBEW_combined": "S_MBEW#FreeText",
}


def _matches_group(filename: str, prefix: str) -> bool:
    return filename.startswith(prefix) and filename.lower().endswith(".csv")


class CombinedFileNotFound(FileNotFoundError):
    pass


def get_preview(name: str, limit: int = 20) -> dict:
    """
    Reads back a combined file that was already saved locally (by a prior
    fetch_and_combine() call) and returns the first `limit` rows.
    `name` is the output name without extension, e.g. "MARC_combined".
    """
    if name not in GROUPS:
        raise CombinedFileNotFound(f"Unknown combined file '{name}'.")

    path = os.path.join(settings.OUTPUT_DIR, f"{name}.csv")
    if not os.path.exists(path):
        raise CombinedFileNotFound(
            f"'{name}.csv' hasn't been created yet - run a fetch first."
        )

    df = pd.read_csv(path)
    preview_df = df.head(limit)

    # NaN isn't valid JSON; swap to None so FastAPI can serialize it cleanly.
    preview_df = preview_df.where(pd.notnull(preview_df), None)

    return {
        "name": name,
        "columns": list(df.columns),
        "rows": preview_df.to_dict(orient="records"),
        "total_rows": int(len(df)),
        "preview_row_count": int(len(preview_df)),
    }


def fetch_and_combine() -> dict:
    client = SharePointClient()
    site_id = client.get_site_id()
    items = client.list_folder_items(site_id, settings.FOLDER_PATH)

    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)

    total_fetched = 0
    combined_files: list[dict] = []

    for output_name, prefix in GROUPS.items():
        matching = [
            item for item in items if _matches_group(item.get("name", ""), prefix)
        ]

        frames = []
        for item in matching:
            content = client.download_file(site_id, item["id"])
            frames.append(pd.read_csv(io.BytesIO(content)))
            total_fetched += 1

        combined_df = (
            pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        )

        save_path = os.path.join(settings.OUTPUT_DIR, f"{output_name}.csv")
        combined_df.to_csv(save_path, index=False)

        combined_files.append(
            {
                "name": output_name,
                "source_files": len(matching),
                "row_count": int(len(combined_df)),
                "saved_path": os.path.abspath(save_path),
            }
        )

    return {
        "total_files_fetched": total_fetched,
        "combined_files": combined_files,
    }
