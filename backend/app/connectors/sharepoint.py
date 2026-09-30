"""
Thin client around Microsoft Graph for talking to a single SharePoint site.

    - authenticates once and caches the token for the life of the client
    - resolves the site ID from the friendly site path
    - lists the contents of a document-library folder, including the change
      metadata the snapshot store uses to detect "nothing changed"
      (eTag, cTag, size, lastModifiedDateTime, file.hashes.quickXorHash)
    - downloads a file's raw bytes by item ID

Every request has a timeout, and 429/503 responses are retried with the
Retry-After delay Graph asks for.
"""
from __future__ import annotations

import time

import msal
import requests

from app.core.config import settings

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_LIST_SELECT = "id,name,eTag,cTag,size,lastModifiedDateTime,file,folder"
_RETRY_STATUSES = {429, 503}
_MAX_RETRIES = 4


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

    def _get(self, url: str, **kwargs) -> requests.Response:
        for attempt in range(_MAX_RETRIES + 1):
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {self._get_token()}"},
                timeout=settings.HTTP_TIMEOUT,
                **kwargs,
            )
            if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                delay = float(resp.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(delay, 60))
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")

    # -- site -------------------------------------------------------------

    def get_site_id(self) -> str:
        url = (
            f"{GRAPH_BASE}/sites/"
            f"{settings.SHAREPOINT_HOSTNAME}:{settings.SITE_PATH}"
        )
        return self._get(url).json()["id"]

    # -- folder contents ----------------------------------------------------

    def list_folder_items(self, site_id: str, folder_path: str) -> list[dict]:
        """Returns every item (files/folders) directly inside folder_path.
        An empty folder_path means 'the root of the Document Library'."""
        if folder_path:
            url = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{folder_path}:/children"
        else:
            url = f"{GRAPH_BASE}/sites/{site_id}/drive/root/children"
        url += f"?$select={_LIST_SELECT}"

        items: list[dict] = []
        while url:
            data = self._get(url).json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")

        return items

    # -- download -----------------------------------------------------------

    def download_file(self, site_id: str, item_id: str) -> bytes:
        url = f"{GRAPH_BASE}/sites/{site_id}/drive/items/{item_id}/content"
        return self._get(url).content
