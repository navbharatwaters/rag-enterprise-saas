"""Confluence connector — API-token-based page sync."""

import logging
from datetime import datetime
from typing import AsyncIterator

import httpx

from src.connectors.base import BaseConnector, ExternalFile
from src.connectors.registry import register_connector

logger = logging.getLogger(__name__)


@register_connector
class ConfluenceConnector(BaseConnector):
    """Connector for Atlassian Confluence Cloud."""

    connector_type = "confluence"

    def _auth(self) -> tuple[str, str]:
        """Return (email, api_token) for HTTP basic auth."""
        return (self.credentials["email"], self.credentials["api_token"])

    def _base_url(self) -> str:
        """Return base URL with trailing slash stripped."""
        return self.config["base_url"].rstrip("/")

    async def validate_credentials(self) -> bool:
        """Verify we can access the configured space."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self._base_url()}/wiki/rest/api/space/{self.config['space_key']}",
                    auth=self._auth(),
                    timeout=15.0,
                )
                return response.status_code == 200
        except Exception:
            logger.debug("Confluence credential validation failed", exc_info=True)
            return False

    async def list_files(
        self,
        since: datetime | None = None,
    ) -> AsyncIterator[ExternalFile]:
        """List pages in the configured space."""
        async with httpx.AsyncClient() as client:
            start = 0
            limit = 50

            while True:
                params = {
                    "spaceKey": self.config["space_key"],
                    "limit": limit,
                    "start": start,
                    "expand": "version",
                }

                response = await client.get(
                    f"{self._base_url()}/wiki/rest/api/content",
                    params=params,
                    auth=self._auth(),
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                results = data.get("results", [])
                for page in results:
                    modified_str = page.get("version", {}).get("when", "")
                    if modified_str:
                        modified = datetime.fromisoformat(modified_str.rstrip("Z"))
                    else:
                        modified = datetime.utcnow()

                    if since and modified < since:
                        continue

                    title = page.get("title", "Untitled")
                    page_id = page["id"]
                    webui = page.get("_links", {}).get("webui", f"/pages/{page_id}")

                    yield ExternalFile(
                        external_id=page_id,
                        name=title,
                        path=f"{self._base_url()}/wiki{webui}",
                        mime_type="text/html",
                        size_bytes=0,  # Content size not known until download
                        modified_at=modified,
                    )

                if len(results) < limit:
                    break
                start += limit

    async def download_file(self, file: ExternalFile) -> tuple[bytes, str]:
        """Download page content as HTML."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self._base_url()}/wiki/rest/api/content/{file.external_id}",
                params={"expand": "body.storage"},
                auth=self._auth(),
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            html_content = data.get("body", {}).get("storage", {}).get("value", "")
            return html_content.encode("utf-8"), f"{file.name}.html"
