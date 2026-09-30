from app.core.config import settings


def get_client():
    """The source of the extracts: SharePoint (default) or a local folder
    that mirrors the library (SOURCE_MODE=local, for testing)."""
    if settings.SOURCE_MODE == "local":
        from app.connectors.local_source import LocalSourceClient
        return LocalSourceClient()
    from app.connectors.sharepoint import SharePointClient
    return SharePointClient()
