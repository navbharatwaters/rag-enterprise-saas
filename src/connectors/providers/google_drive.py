"""Google Drive connector — OAuth-based file sync."""

import asyncio
import logging
from datetime import datetime
from typing import AsyncIterator
from urllib.parse import urlencode

import httpx
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from src.connectors.base import BaseConnector, ExternalFile
from src.connectors.registry import register_connector
from src.core.config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Google Docs MIME types that need export
GOOGLE_DOCS_EXPORT_MAP = {
    "application/vnd.google-apps.document": "application/pdf",
    "application/vnd.google-apps.spreadsheet": "application/pdf",
    "application/vnd.google-apps.presentation": "application/pdf",
}


@register_connector
class GoogleDriveConnector(BaseConnector):
    """Connector for Google Drive via OAuth2."""

    connector_type = "google_drive"

    @property
    def supports_oauth(self) -> bool:
        return True

    def _get_credentials(self) -> Credentials:
        """Build Google OAuth Credentials from stored tokens."""
        return Credentials(
            token=self.credentials.get("access_token"),
            refresh_token=self.credentials.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            scopes=SCOPES,
        )

    def _build_service(self):
        """Build the Google Drive API service."""
        creds = self._get_credentials()
        return build("drive", "v3", credentials=creds)

    async def validate_credentials(self) -> bool:
        """Verify that the stored OAuth tokens are still valid."""
        try:
            service = self._build_service()
            await asyncio.to_thread(
                service.about().get(fields="user").execute
            )
            return True
        except Exception:
            logger.debug("Google Drive credential validation failed", exc_info=True)
            return False

    async def list_files(
        self,
        since: datetime | None = None,
    ) -> AsyncIterator[ExternalFile]:
        """List files in the configured folder (or root)."""
        service = self._build_service()

        query_parts = [
            "trashed = false",
            "mimeType != 'application/vnd.google-apps.folder'",
        ]

        folder_id = self.config.get("folder_id")
        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")

        if since:
            query_parts.append(f"modifiedTime > '{since.isoformat()}'")

        query = " and ".join(query_parts)
        page_token = None
        include_team = self.config.get("include_team_drives", False)

        while True:
            response = await asyncio.to_thread(
                service.files()
                .list(
                    q=query,
                    pageSize=100,
                    pageToken=page_token,
                    fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, md5Checksum)",
                    includeItemsFromAllDrives=include_team,
                    supportsAllDrives=True,
                )
                .execute
            )

            for file in response.get("files", []):
                modified_str = file.get("modifiedTime", "")
                if modified_str:
                    modified_at = datetime.fromisoformat(modified_str.rstrip("Z"))
                else:
                    modified_at = datetime.utcnow()

                yield ExternalFile(
                    external_id=file["id"],
                    name=file["name"],
                    path=f"/drive/{file['id']}",
                    mime_type=file.get("mimeType", "application/octet-stream"),
                    size_bytes=int(file.get("size", 0)),
                    modified_at=modified_at,
                    hash=file.get("md5Checksum"),
                )

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    async def download_file(self, file: ExternalFile) -> tuple[bytes, str]:
        """Download a file. Exports Google Docs as PDF."""
        service = self._build_service()

        if file.mime_type in GOOGLE_DOCS_EXPORT_MAP:
            export_mime = GOOGLE_DOCS_EXPORT_MAP[file.mime_type]
            content = await asyncio.to_thread(
                service.files()
                .export(fileId=file.external_id, mimeType=export_mime)
                .execute
            )
            # Adjust filename for exported docs
            name = file.name
            if not name.endswith(".pdf"):
                name = f"{name}.pdf"
            return content, name

        content = await asyncio.to_thread(
            service.files().get_media(fileId=file.external_id).execute
        )
        return content, file.name

    async def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        """Generate the Google OAuth2 authorization URL."""
        params = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """Exchange authorization code for access + refresh tokens."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            data = response.json()

        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "token_type": data.get("token_type", "Bearer"),
            "expires_in": data.get("expires_in"),
        }
