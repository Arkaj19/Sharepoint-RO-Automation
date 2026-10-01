"""
Fetches raw SharePoint extracts and combines them.
Outputs are routed to specific folders: /downloads/{MARC|MBEW}/{ECC|S4}/
"""
from __future__ import annotations

import io
import os
import pandas as pd

from app.core.config import settings
from app.services.sharepoint_service import SharePointClient

# --- Configuration Mapping ---
# Maps output folder -> prefix to match in SharePoint
S4_GROUPS = {
    "MARC": "S_MARC#FreeText",
    "MBEW": "S_MBEW#FreeText",
}

# Maps output folder -> prefix to match in SharePoint for ECC files
ECC_GROUPS = {
    "MARC": "MARC_DAP",
    "MBEW": "MBEW_DAP",
}

OUTPUT_EXT = ".csv"

class CombinedFileNotFound(FileNotFoundError):
    pass

def _get_output_dir(family: str, source_type: str) -> str:
    """Returns the path: settings.OUTPUT_DIR/MARC/S4 or settings.OUTPUT_DIR/MBEW/ECC"""
    return os.path.join(settings.OUTPUT_DIR, family, source_type)

def _matches(filename: str, prefix: str, extensions: tuple) -> bool:
    return filename.startswith(prefix) and filename.lower().endswith(extensions)

# --- PREVIEW & DOWNLOAD ---

def get_preview(family: str, source_type: str, limit: int = 20) -> dict:
    """
    Reads back a combined file. 
    Example: get_preview("MARC", "S4")
    """
    if family not in S4_GROUPS:
        raise CombinedFileNotFound(f"Unknown family '{family}'.")
    if source_type not in ["S4", "ECC"]:
        raise CombinedFileNotFound(f"Unknown source type '{source_type}'.")

    folder = _get_output_dir(family, source_type)
    path = os.path.join(folder, f"{family}_{source_type}_combined{OUTPUT_EXT}")
    
    if not os.path.exists(path):
        raise CombinedFileNotFound(f"File for {family} {source_type} hasn't been created yet.")

    df = pd.read_csv(path)
    preview_df = df.head(limit)
    preview_df = preview_df.where(pd.notnull(preview_df), None)

    return {
        "name": f"{family}_{source_type}",
        "columns": list(df.columns),
        "rows": preview_df.to_dict(orient="records"),
        "total_rows": int(len(df)),
        "preview_row_count": int(len(preview_df)),
    }

def build_xlsx_bytes(family: str, source_type: str) -> io.BytesIO:
    """Reads stored CSV and converts to XLSX in memory."""
    folder = _get_output_dir(family, source_type)
    csv_path = os.path.join(folder, f"{family}_{source_type}_combined{OUTPUT_EXT}")
    
    if not os.path.exists(csv_path):
        raise CombinedFileNotFound(f"File for {family} {source_type} not found.")

    df = pd.read_csv(csv_path)
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)
    return buffer

# --- FETCH OPERATIONS ---

def fetch_s4_files() -> dict:
    """Fetches S4 files from the 'Databricks Files' folder."""
    client = SharePointClient()
    site_id = client.get_site_id()
    # S4 files live in the specific folder
    items = client.list_folder_items(site_id, settings.FOLDER_PATH)

    total_fetched = 0
    combined_files = []

    for family, prefix in S4_GROUPS.items():
        # Match only CSVs for S4
        matching = [item for item in items if _matches(item.get("name", ""), prefix, (".csv",))]
        
        frames = []
        for item in matching:
            content = client.download_file(site_id, item["id"])
            frames.append(pd.read_csv(io.BytesIO(content), low_memory=False))
            total_fetched += 1

        combined_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        
        # Save to: output/MARC/S4/ or output/MBEW/S4/
        save_dir = _get_output_dir(family, "S4")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, f"{family}_S4_combined{OUTPUT_EXT}")
        combined_df.to_csv(save_path, index=False)

        combined_files.append({
            "name": f"{family}_S4",
            "family": family,
            "source_type": "S4",
            "source_files": len(matching),
            "row_count": int(len(combined_df)),
            "saved_path": os.path.abspath(save_path),
        })

    return {"total_files_fetched": total_fetched, "combined_files": combined_files}


def fetch_ecc_files() -> dict:
    """Fetches ECC (DAP) files from the root Documents folder."""
    client = SharePointClient()
    site_id = client.get_site_id()
    # ECC files live in the root folder (empty string path)
    items = client.list_folder_items(site_id, settings.ECC_FOLDER_PATH)

    total_fetched = 0
    combined_files = []

    for family, prefix in ECC_GROUPS.items():
        # Match only XLSX/XLS for ECC (since they are Excel files)
        matching = [item for item in items if _matches(item.get("name", ""), prefix, (".xlsx", ".xls"))]
        
        frames = []
        for item in matching:
            content = client.download_file(site_id, item["id"])
            # Read ECC files as Excel
            frames.append(pd.read_excel(io.BytesIO(content), engine="openpyxl"))
            total_fetched += 1

        combined_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        
        # Save to: output/MARC/ECC/ or output/MBEW/ECC/
        save_dir = _get_output_dir(family, "ECC")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, f"{family}_ECC_combined{OUTPUT_EXT}")
        combined_df.to_csv(save_path, index=False)

        combined_files.append({
            "name": f"{family}_ECC",
            "family": family,
            "source_type": "ECC",
            "source_files": len(matching),
            "row_count": int(len(combined_df)),
            "saved_path": os.path.abspath(save_path),
        })

    return {"total_files_fetched": total_fetched, "combined_files": combined_files}