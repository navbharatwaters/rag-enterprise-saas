"""Connector service — CRUD, credential management, sync triggering."""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.connectors.encryption import decrypt_credentials, encrypt_credentials
from src.connectors.registry import get_connector_instance
from src.connectors.schemas import ConnectorStatus, SyncFrequency

logger = logging.getLogger(__name__)


def calculate_next_sync(frequency: str, from_time: datetime | None = None) -> datetime | None:
    """Calculate the next sync time based on frequency."""
    if frequency == SyncFrequency.MANUAL:
        return None

    base = from_time or datetime.utcnow()
    deltas = {
        SyncFrequency.HOURLY: timedelta(hours=1),
        SyncFrequency.DAILY: timedelta(days=1),
        SyncFrequency.WEEKLY: timedelta(weeks=1),
    }
    delta = deltas.get(frequency)
    if delta is None:
        return None
    return base + delta


class ConnectorService:
    """High-level connector operations."""

    async def create_connector(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        connector_type: str,
        name: str,
        config: dict,
        credentials: dict | None = None,
        sync_frequency: str = "daily",
        file_types: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> dict:
        """Create a new connector with encrypted credentials."""
        encrypted = encrypt_credentials(credentials) if credentials else None
        next_sync = calculate_next_sync(sync_frequency)

        result = await db.execute(
            text("""
                INSERT INTO connectors (
                    tenant_id, connector_type, name, config,
                    credentials_encrypted, sync_frequency, next_sync_at,
                    status, file_types, exclude_patterns
                ) VALUES (
                    :tenant_id, :connector_type, :name, :config::jsonb,
                    :credentials_encrypted, :sync_frequency, :next_sync_at,
                    :status, :file_types, :exclude_patterns
                )
                RETURNING id, created_at, updated_at
            """),
            {
                "tenant_id": tenant_id,
                "connector_type": connector_type,
                "name": name,
                "config": __import__("json").dumps(config),
                "credentials_encrypted": encrypted,
                "sync_frequency": sync_frequency,
                "next_sync_at": next_sync,
                "status": ConnectorStatus.ACTIVE if credentials else ConnectorStatus.PENDING,
                "file_types": file_types,
                "exclude_patterns": exclude_patterns,
            },
        )
        row = result.fetchone()
        await db.flush()

        return {
            "id": row.id,
            "connector_type": connector_type,
            "name": name,
            "config": config,
            "status": ConnectorStatus.ACTIVE if credentials else ConnectorStatus.PENDING,
            "sync_frequency": sync_frequency,
            "next_sync_at": next_sync,
            "file_types": file_types,
            "exclude_patterns": exclude_patterns,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def list_connectors(
        self,
        db: AsyncSession,
        tenant_id: UUID,
    ) -> list[dict]:
        """List all connectors for a tenant."""
        result = await db.execute(
            text("""
                SELECT id, connector_type, name, config, status,
                       sync_frequency, last_sync_at, next_sync_at,
                       error_message, file_types, exclude_patterns,
                       created_at, updated_at
                FROM connectors
                WHERE tenant_id = :tenant_id
                ORDER BY created_at DESC
            """),
            {"tenant_id": tenant_id},
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_connector(
        self,
        db: AsyncSession,
        connector_id: UUID,
    ) -> dict | None:
        """Get a single connector by ID."""
        result = await db.execute(
            text("""
                SELECT id, tenant_id, connector_type, name, config,
                       credentials_encrypted, status, sync_frequency,
                       last_sync_at, next_sync_at, error_message,
                       file_types, exclude_patterns, created_at, updated_at
                FROM connectors
                WHERE id = :id
            """),
            {"id": connector_id},
        )
        row = result.fetchone()
        if not row:
            return None
        return dict(row._mapping)

    async def update_connector(
        self,
        db: AsyncSession,
        connector_id: UUID,
        updates: dict,
    ) -> dict | None:
        """Update connector fields."""
        # Build SET clause dynamically from provided updates
        allowed = {
            "name", "config", "sync_frequency", "status",
            "file_types", "exclude_patterns", "error_message",
            "last_sync_at", "next_sync_at",
        }
        set_parts = []
        params: dict = {"id": connector_id}

        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "config":
                set_parts.append(f"{key} = :{key}::jsonb")
                params[key] = __import__("json").dumps(value)
            else:
                set_parts.append(f"{key} = :{key}")
                params[key] = value

        if not set_parts:
            return await self.get_connector(db, connector_id)

        # Recalculate next_sync if frequency changed
        if "sync_frequency" in updates and "next_sync_at" not in updates:
            next_sync = calculate_next_sync(updates["sync_frequency"])
            set_parts.append("next_sync_at = :next_sync_at")
            params["next_sync_at"] = next_sync

        set_clause = ", ".join(set_parts)
        await db.execute(
            text(f"UPDATE connectors SET {set_clause} WHERE id = :id"),
            params,
        )
        await db.flush()
        return await self.get_connector(db, connector_id)

    async def delete_connector(
        self,
        db: AsyncSession,
        connector_id: UUID,
    ) -> bool:
        """Delete a connector and cascade to synced_files/sync_history."""
        result = await db.execute(
            text("DELETE FROM connectors WHERE id = :id"),
            {"id": connector_id},
        )
        await db.flush()
        return result.rowcount > 0

    async def get_sync_history(
        self,
        db: AsyncSession,
        connector_id: UUID,
        limit: int = 20,
    ) -> list[dict]:
        """Get sync history for a connector, most recent first."""
        result = await db.execute(
            text("""
                SELECT id, connector_id, started_at, completed_at, status,
                       files_found, files_processed, files_skipped,
                       files_failed, bytes_processed, error_message
                FROM sync_history
                WHERE connector_id = :connector_id
                ORDER BY started_at DESC
                LIMIT :limit
            """),
            {"connector_id": connector_id, "limit": limit},
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_decrypted_credentials(
        self,
        db: AsyncSession,
        connector_id: UUID,
    ) -> dict | None:
        """Load and decrypt credentials for a connector."""
        result = await db.execute(
            text("SELECT credentials_encrypted FROM connectors WHERE id = :id"),
            {"id": connector_id},
        )
        row = result.fetchone()
        if not row or not row.credentials_encrypted:
            return None
        return decrypt_credentials(row.credentials_encrypted)

    async def trigger_sync(
        self,
        db: AsyncSession,
        connector_id: UUID,
    ) -> None:
        """Mark connector as syncing. Caller is responsible for queuing the ARQ job."""
        await db.execute(
            text("""
                UPDATE connectors
                SET status = :status, error_message = NULL
                WHERE id = :id
            """),
            {"id": connector_id, "status": ConnectorStatus.SYNCING},
        )
        await db.flush()


def get_connector_service() -> ConnectorService:
    """Get ConnectorService instance."""
    return ConnectorService()
