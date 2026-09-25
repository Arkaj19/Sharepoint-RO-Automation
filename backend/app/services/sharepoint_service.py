"""
Thin client around Microsoft Graph for talking to a single SharePoint site.

This is the refined, reusable version of the original fetch.py script:
    - authenticates once and caches the token for the life of the request
    - resolves the site ID from the friendly site path
    - lists the contents of a document-library folder (so callers don't
      need to know exact filenames up front)
    - downloads a file's raw bytes by item ID
"""
from __future__ import annotations

import requests
import msal

from app.core.config import settings

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class SharePointAuthError(RuntimeError):
    pass


class SharePointClient:
    def __init__(self) -> None:
        self._token: str | None = None

    # -- auth -----------------------------------------------------------

    def _get_token(self) -> str:
        if self._token:
            return self._token

        authority = f"https://login.microsoftonline.com/{settings.TENANT_ID}"
        app = msal.ConfidentialClientApplication(
            client_id=settings.CLIENT_ID,
            client_credential=settings.CLIENT_SECRET,
            authority=authority,
        )
        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )

        if "access_token" not in result:
            raise SharePointAuthError(
                f"Failed to acquire token: {result.get('error')} - "
                f"{result.get('error_description')}"
            )

        self._token = result["access_token"]
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # -- site -------------------------------------------------------------

    def get_site_id(self) -> str:
        url = (
            f"{GRAPH_BASE}/sites/"
            f"{settings.SHAREPOINT_HOSTNAME}:{settings.SITE_PATH}"
        )
        resp = requests.get(url, headers=self._headers())
        resp.raise_for_status()
        return resp.json()["id"]

    # -- folder contents ----------------------------------------------------

    def list_folder_items(self, site_id: str, folder_path: str) -> list[dict]:
        """Returns every item (files/folders) directly inside folder_path."""
        url = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{folder_path}:/children"
        items: list[dict] = []

        while url:
            resp = requests.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")

        return items

    # -- download -----------------------------------------------------------

    def download_file(self, site_id: str, item_id: str) -> bytes:
        url = f"{GRAPH_BASE}/sites/{site_id}/drive/items/{item_id}/content"
        resp = requests.get(url, headers=self._headers())
        resp.raise_for_status()
        return resp.content
