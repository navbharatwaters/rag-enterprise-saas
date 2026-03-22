import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TenantMixin, TimestampMixin


class Connector(TenantMixin, TimestampMixin, Base):
    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    # Type and name
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Configuration (type-specific, not secret)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Credentials (encrypted with Fernet)
    credentials_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # Sync settings
    sync_frequency: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'daily'")
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Filtering
    file_types: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    exclude_patterns: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    __table_args__ = (
        Index("idx_connectors_tenant", "tenant_id"),
        Index("idx_connectors_type", "tenant_id", "connector_type"),
        Index(
            "idx_connectors_next_sync",
            "next_sync_at",
            postgresql_where=text("status = 'active'"),
        ),
    )


class SyncHistory(Base):
    __tablename__ = "sync_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False
    )

    # Sync details
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # Stats
    files_found: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    files_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    files_skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    files_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    bytes_processed: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    # Errors
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("idx_sync_history_connector", "connector_id"),
        Index("idx_sync_history_tenant", "tenant_id"),
        Index("idx_sync_history_started", "connector_id", "started_at"),
    )


class SyncedFile(Base):
    __tablename__ = "synced_files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    # External file info
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    external_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    external_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Sync state
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )

    __table_args__ = (
        UniqueConstraint("connector_id", "external_id", name="uq_synced_files_connector_ext"),
        Index("idx_synced_files_connector", "connector_id"),
        Index("idx_synced_files_tenant", "tenant_id"),
        Index("idx_synced_files_document", "document_id"),
    )
