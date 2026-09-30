"""
A folder on disk that stands in for the SharePoint document library
(SOURCE_MODE=local). LOCAL_SOURCE_ROOT mirrors the library: its own files
are the library root, sub-folders are SharePoint folders ("Databricks Files").

Items are shaped like Graph items, so the refresh treats them the same way.
There is no content hash; cTag is built from size + modification time, which
changes whenever a file is saved (the refresh then compares SHA-256s).
Item IDs are relative to the root ("localsrc:<folder>/<name>"), so a copied
or moved source folder keeps the same fingerprints.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings

PREFIX = "localsrc:"


class LocalSourceError(RuntimeError):
    pass


class LocalSourceClient:
    def __init__(self, root: str | None = None) -> None:
        raw = root if root is not None else settings.LOCAL_SOURCE_ROOT
        if not raw:
            raise LocalSourceError("SOURCE_MODE=local needs LOCAL_SOURCE_ROOT (a folder that mirrors the library).")
        self.root = Path(raw).resolve()
        if not self.root.is_dir():
            raise LocalSourceError(f"LOCAL_SOURCE_ROOT '{self.root}' is not a folder.")

    def get_site_id(self) -> str:
        return "local"

    def list_folder_items(self, site_id: str, folder_path: str) -> list[dict]:
        folder = self.root / folder_path if folder_path else self.root
        if not folder.is_dir():
            raise LocalSourceError(f"Folder '{folder_path}' does not exist under LOCAL_SOURCE_ROOT.")
        items = []
        for f in sorted(folder.iterdir()):
            st = f.stat()
            item = {"id": PREFIX + f.relative_to(self.root).as_posix(), "name": f.name,
                    "lastModifiedDateTime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()}
            if f.is_file():
                item.update(size=st.st_size, cTag=f"local:{st.st_size}:{st.st_mtime_ns}", file={})
            else:
                item["folder"] = {}
            items.append(item)
        return items

    def download_file(self, site_id: str, item_id: str) -> bytes:
        if not item_id.startswith(PREFIX):
            raise LocalSourceError(f"Not a local source item: {item_id}")
        path = (self.root / item_id[len(PREFIX):]).resolve()
        if self.root not in path.parents:
            raise LocalSourceError(f"{item_id} is outside LOCAL_SOURCE_ROOT")
        return path.read_bytes()
