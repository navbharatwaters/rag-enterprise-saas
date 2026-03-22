"""Pydantic schemas for connector API."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class ConnectorType(str, Enum):
    """Supported connector types."""

    GOOGLE_DRIVE = "google_drive"
    S3 = "s3"
    CONFLUENCE = "confluence"


class ConnectorStatus(str, Enum):
    """Connector lifecycle status."""

    PENDING = "pending"
    ACTIVE = "active"
    SYNCING = "syncing"
    ERROR = "error"
    DISABLED = "disabled"


class SyncFrequency(str, Enum):
    """Sync schedule options."""

    MANUAL = "manual"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


# --- Request models ---


class CreateConnectorRequest(BaseModel):
    """Request to create a new connector."""

    connector_type: ConnectorType
    name: str = Field(..., min_length=1, max_length=255)
    config: dict = Field(default_factory=dict)
    credentials: dict | None = None  # For non-OAuth connectors (S3, Confluence)
    sync_frequency: SyncFrequency = SyncFrequency.DAILY
    file_types: list[str] | None = None
    exclude_patterns: list[str] | None = None


class UpdateConnectorRequest(BaseModel):
    """Request to update an existing connector."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict | None = None
    sync_frequency: SyncFrequency | None = None
    file_types: list[str] | None = None
    exclude_patterns: list[str] | None = None
    status: ConnectorStatus | None = None


# --- Response models ---


class ConnectorResponse(BaseModel):
    """Connector details returned to the client. Credentials excluded."""

    id: UUID
    connector_type: ConnectorType
    name: str
    status: ConnectorStatus
    config: dict
    sync_frequency: SyncFrequency
    last_sync_at: datetime | None = None
    next_sync_at: datetime | None = None
    error_message: str | None = None
    file_types: list[str] | None = None
    exclude_patterns: list[str] | None = None
    created_at: datetime
    updated_at: datetime


class ConnectorListResponse(BaseModel):
    """Paginated list of connectors."""

    items: list[ConnectorResponse]
    total: int


class SyncHistoryResponse(BaseModel):
    """Single sync history entry."""

    id: UUID
    connector_id: UUID
    started_at: datetime
    completed_at: datetime | None = None
    status: str
    files_found: int
    files_processed: int
    files_skipped: int
    files_failed: int
    bytes_processed: int
    error_message: str | None = None


class SyncHistoryListResponse(BaseModel):
    """List of sync history entries."""

    items: list[SyncHistoryResponse]
    total: int


class OAuthStartResponse(BaseModel):
    """Response when starting OAuth flow."""

    authorization_url: str
    state: str


class TriggerSyncResponse(BaseModel):
    """Response when triggering a manual sync."""

    message: str = "Sync job queued"
    connector_id: UUID
