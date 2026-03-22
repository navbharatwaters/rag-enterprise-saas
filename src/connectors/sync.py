"""Sync logic — run connector sync, process files, track state."""

import hashlib
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.connectors.base import ExternalFile, SyncResult
from src.connectors.encryption import decrypt_credentials
from src.connectors.registry import get_connector_instance
from src.connectors.schemas import ConnectorStatus
from src.connectors.service import calculate_next_sync

logger = logging.getLogger(__name__)


async def run_connector_sync(
    db: AsyncSession,
    connector_id: UUID,
) -> SyncResult:
    """Run a full sync for a connector.

    Steps:
    1. Load connector + decrypt credentials.
    2. Create a sync_history entry (status='running').
    3. List files from the provider.
    4. Process each file (hash check, download, upsert synced_files).
    5. Update sync_history with results.
    6. Update connector status + next_sync_at.
    """
    result = SyncResult()

    # Load connector
    row = await _get_connector_row(db, connector_id)
    if not row:
        logger.error("Connector %s not found", connector_id)
        return result

    connector_type = row.connector_type
    config = row.config if isinstance(row.config, dict) else {}
    sync_frequency = row.sync_frequency or "daily"
    last_sync_at = row.last_sync_at
    tenant_id = row.tenant_id

    # Decrypt credentials
    creds = {}
    if row.credentials_encrypted:
        creds = decrypt_credentials(row.credentials_encrypted)

    # Mark connector as syncing
    await db.execute(
        text("UPDATE connectors SET status = :status, error_message = NULL WHERE id = :id"),
        {"id": connector_id, "status": ConnectorStatus.SYNCING},
    )

    # Create sync_history entry
    sync_history_id = await _create_sync_history(db, connector_id)
    await db.flush()

    try:
        connector = get_connector_instance(connector_type, config, creds)

        # List files (incremental: only since last sync)
        async for ext_file in connector.list_files(since=last_sync_at):
            result.files_found += 1
            try:
                processed = await process_file(
                    db, connector_id, connector, ext_file
                )
                if processed:
                    result.files_processed += 1
                else:
                    result.files_skipped += 1
            except Exception as exc:
                result.files_failed += 1
                result.errors.append(f"{ext_file.name}: {exc}")
                logger.warning("Failed to process file %s: %s", ext_file.name, exc)

        # Update connector — success
        next_sync = calculate_next_sync(sync_frequency)
        await db.execute(
            text("""
                UPDATE connectors
                SET status = :status, last_sync_at = :now, next_sync_at = :next_sync,
                    error_message = NULL
                WHERE id = :id
            """),
            {
                "id": connector_id,
                "status": ConnectorStatus.ACTIVE,
                "now": datetime.now(timezone.utc),
                "next_sync": next_sync,
            },
        )

        # Update sync_history — completed
        await _complete_sync_history(db, sync_history_id, "completed", result)
        await db.flush()

    except Exception as exc:
        logger.error("Sync failed for connector %s: %s", connector_id, exc)
        result.errors.append(str(exc))

        # Update connector — error
        await db.execute(
            text("""
                UPDATE connectors
                SET status = :status, error_message = :error
                WHERE id = :id
            """),
            {
                "id": connector_id,
                "status": ConnectorStatus.ERROR,
                "error": str(exc)[:500],
            },
        )

        # Update sync_history — failed
        await _complete_sync_history(db, sync_history_id, "failed", result, str(exc))
        await db.flush()

    return result


async def process_file(
    db: AsyncSession,
    connector_id: UUID,
    connector,
    ext_file: ExternalFile,
) -> bool:
    """Process a single file: check hash, download if changed, update synced_files.

    Returns True if the file was downloaded/processed, False if skipped (unchanged).
    """
    # Check existing synced_file record
    existing = await _get_synced_file(db, connector_id, ext_file.external_id)

    # Hash check — skip if file hasn't changed
    file_hash = ext_file.hash or _compute_metadata_hash(ext_file)
    if existing and existing.external_hash == file_hash:
        return False

    # Download the file
    content, filename = await connector.download_file(ext_file)
    content_hash = hashlib.sha256(content).hexdigest()

    # Double-check with content hash
    if existing and existing.external_hash == content_hash:
        return False

    # Upsert synced_files record
    await _upsert_synced_file(
        db, connector_id, ext_file, content_hash, len(content)
    )

    return True


def _compute_metadata_hash(ext_file: ExternalFile) -> str:
    """Compute a hash from file metadata when no provider hash is available."""
    parts = f"{ext_file.external_id}:{ext_file.modified_at.isoformat()}:{ext_file.size_bytes}"
    return hashlib.sha256(parts.encode()).hexdigest()


async def _get_connector_row(db: AsyncSession, connector_id: UUID):
    """Load raw connector row."""
    result = await db.execute(
        text("""
            SELECT id, tenant_id, connector_type, config, credentials_encrypted,
                   sync_frequency, last_sync_at, status
            FROM connectors WHERE id = :id
        """),
        {"id": connector_id},
    )
    return result.fetchone()


async def _create_sync_history(db: AsyncSession, connector_id: UUID) -> UUID:
    """Create a new sync_history entry with status 'running'."""
    result = await db.execute(
        text("""
            INSERT INTO sync_history (connector_id, started_at, status)
            VALUES (:connector_id, :started_at, 'running')
            RETURNING id
        """),
        {"connector_id": connector_id, "started_at": datetime.now(timezone.utc)},
    )
    return result.fetchone().id


async def _complete_sync_history(
    db: AsyncSession,
    sync_history_id: UUID,
    status: str,
    result: SyncResult,
    error_message: str | None = None,
) -> None:
    """Update sync_history entry with results."""
    await db.execute(
        text("""
            UPDATE sync_history
            SET completed_at = :completed_at, status = :status,
                files_found = :files_found, files_processed = :files_processed,
                files_skipped = :files_skipped, files_failed = :files_failed,
                bytes_processed = :bytes_processed, error_message = :error_message
            WHERE id = :id
        """),
        {
            "id": sync_history_id,
            "completed_at": datetime.now(timezone.utc),
            "status": status,
            "files_found": result.files_found,
            "files_processed": result.files_processed,
            "files_skipped": result.files_skipped,
            "files_failed": result.files_failed,
            "bytes_processed": result.bytes_processed,
            "error_message": error_message,
        },
    )


async def _get_synced_file(db: AsyncSession, connector_id: UUID, external_id: str):
    """Get existing synced_file record."""
    result = await db.execute(
        text("""
            SELECT id, external_id, external_hash, sync_status
            FROM synced_files
            WHERE connector_id = :connector_id AND external_id = :external_id
        """),
        {"connector_id": connector_id, "external_id": external_id},
    )
    return result.fetchone()


async def _upsert_synced_file(
    db: AsyncSession,
    connector_id: UUID,
    ext_file: ExternalFile,
    content_hash: str,
    size_bytes: int,
) -> None:
    """Insert or update a synced_files record."""
    await db.execute(
        text("""
            INSERT INTO synced_files (
                connector_id, external_id, external_path,
                external_modified_at, external_hash, file_size_bytes,
                sync_status, last_synced_at
            ) VALUES (
                :connector_id, :external_id, :external_path,
                :external_modified_at, :external_hash, :file_size_bytes,
                'synced', :last_synced_at
            )
            ON CONFLICT (connector_id, external_id) DO UPDATE SET
                external_path = EXCLUDED.external_path,
                external_modified_at = EXCLUDED.external_modified_at,
                external_hash = EXCLUDED.external_hash,
                file_size_bytes = EXCLUDED.file_size_bytes,
                sync_status = 'synced',
                last_synced_at = EXCLUDED.last_synced_at
        """),
        {
            "connector_id": connector_id,
            "external_id": ext_file.external_id,
            "external_path": ext_file.path,
            "external_modified_at": ext_file.modified_at,
            "external_hash": content_hash,
            "file_size_bytes": size_bytes,
            "last_synced_at": datetime.now(timezone.utc),
        },
    )
