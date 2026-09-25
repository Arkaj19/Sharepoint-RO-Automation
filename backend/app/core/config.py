"""
Central app configuration, loaded from environment variables (.env).
Keeps SharePoint / Graph credentials out of source code.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Azure AD app registration ("SharePoint File Fetcher")
    TENANT_ID: str = os.getenv("SP_TENANT_ID", "")
    CLIENT_ID: str = os.getenv("SP_CLIENT_ID", "")
    CLIENT_SECRET: str = os.getenv("SP_CLIENT_SECRET", "")

    # SharePoint location
    SHAREPOINT_HOSTNAME: str = os.getenv("SP_HOSTNAME", "adam702.sharepoint.com")
    SITE_PATH: str = os.getenv("SP_SITE_PATH", "/sites/ROSharePointAutomation")
    FOLDER_PATH: str = os.getenv("SP_FOLDER_PATH", "Databricks Files")

    # Where combined output files get saved locally
    OUTPUT_DIR: str = os.getenv("SP_OUTPUT_DIR", "./data/combined")

    # CORS - the Vite dev server origin
    FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")


settings = Settings()
