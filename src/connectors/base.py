"""Base classes for connectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator


@dataclass
class ExternalFile:
    """File from an external source."""

    external_id: str
    name: str
    path: str
    mime_type: str
    size_bytes: int
    modified_at: datetime
    hash: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    """Result of a sync operation."""

    files_found: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    bytes_processed: int = 0
    errors: list[str] = field(default_factory=list)


class BaseConnector(ABC):
    """Abstract base class for all connectors.

    Subclasses must set ``connector_type`` as a class attribute and implement
    all abstract methods.
    """

    connector_type: str = ""

    def __init__(self, config: dict, credentials: dict):
        self.config = config
        self.credentials = credentials

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """Check if credentials are valid and usable."""

    @abstractmethod
    async def list_files(
        self,
        since: datetime | None = None,
    ) -> AsyncIterator[ExternalFile]:
        """List files, optionally only those modified since *since*."""
        # Must be an async generator in subclasses
        yield  # pragma: no cover

    @abstractmethod
    async def download_file(
        self,
        file: ExternalFile,
    ) -> tuple[bytes, str]:
        """Download file content. Returns ``(bytes, filename)``."""

    async def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        """Get OAuth authorization URL. Override for OAuth connectors."""
        raise NotImplementedError(f"{self.connector_type} does not support OAuth")

    async def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """Exchange OAuth code for credentials. Override for OAuth connectors."""
        raise NotImplementedError(f"{self.connector_type} does not support OAuth")

    @property
    def supports_oauth(self) -> bool:
        """Whether this connector type uses OAuth for authentication."""
        return False
