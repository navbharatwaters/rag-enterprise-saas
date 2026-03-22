"""MinIO document storage client.

Stores document files in a tenant-isolated path structure:
  {bucket}/{tenant_id}/{document_id}/original.{ext}

The MinIO SDK is synchronous, so methods run blocking I/O in a thread
via asyncio.to_thread to keep the async event loop unblocked.
"""

import asyncio
import io
import logging
from pathlib import Path
from uuid import UUID

from minio import Minio
from minio.error import S3Error

from src.core.config import settings

logger = logging.getLogger(__name__)


class DocumentStorage:
    """MinIO storage client for tenant-scoped documents."""

    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        secure: bool | None = None,
    ):
        self.endpoint = endpoint or settings.MINIO_ENDPOINT
        self.bucket = bucket or settings.MINIO_BUCKET
        _secure = secure if secure is not None else settings.MINIO_SECURE

        # Strip protocol prefix if present (Minio client doesn't want it)
        clean_endpoint = self.endpoint
        if clean_endpoint.startswith("https://"):
            clean_endpoint = clean_endpoint[8:]
            _secure = True
        elif clean_endpoint.startswith("http://"):
            clean_endpoint = clean_endpoint[7:]
            _secure = False

        self.client = Minio(
            clean_endpoint,
            access_key=access_key or settings.MINIO_ACCESS_KEY,
            secret_key=secret_key or settings.MINIO_SECRET_KEY,
            secure=_secure,
        )

    async def ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist."""
        exists = await asyncio.to_thread(self.client.bucket_exists, self.bucket)
        if not exists:
            await asyncio.to_thread(self.client.make_bucket, self.bucket)
            logger.info("Created bucket: %s", self.bucket)

    async def upload(
        self,
        tenant_id: UUID,
        document_id: UUID,
        data: bytes | io.BytesIO,
        filename: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload a file to tenant-scoped storage.

        Args:
            tenant_id: Tenant UUID.
            document_id: Document UUID.
            data: File contents as bytes or BytesIO.
            filename: Original filename (used for extension).
            content_type: MIME type of the file.

        Returns:
            Storage path (object name) within the bucket.
        """
        ext = Path(filename).suffix
        object_name = f"{tenant_id}/{document_id}/original{ext}"

        if isinstance(data, bytes):
            data = io.BytesIO(data)

        size = data.seek(0, 2)  # Get size
        data.seek(0)

        await asyncio.to_thread(
            self.client.put_object,
            self.bucket,
            object_name,
            data,
            size,
            content_type=content_type,
        )

        logger.info("Uploaded %s (%d bytes)", object_name, size)
        return object_name

    async def download(self, path: str) -> bytes:
        """Download a file from storage.

        Args:
            path: Object name (storage path) within the bucket.

        Returns:
            File contents as bytes.
        """
        response = await asyncio.to_thread(
            self.client.get_object, self.bucket, path
        )
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def delete(self, tenant_id: UUID, document_id: UUID) -> int:
        """Delete all files for a document.

        Args:
            tenant_id: Tenant UUID.
            document_id: Document UUID.

        Returns:
            Number of objects deleted.
        """
        prefix = f"{tenant_id}/{document_id}/"
        objects = await asyncio.to_thread(
            lambda: list(self.client.list_objects(self.bucket, prefix=prefix, recursive=True))
        )

        count = 0
        for obj in objects:
            await asyncio.to_thread(
                self.client.remove_object, self.bucket, obj.object_name
            )
            count += 1

        logger.info("Deleted %d objects with prefix %s", count, prefix)
        return count

    async def exists(self, path: str) -> bool:
        """Check if an object exists in storage."""
        try:
            await asyncio.to_thread(self.client.stat_object, self.bucket, path)
            return True
        except S3Error:
            return False


# Module-level singleton (lazy init)
_storage: DocumentStorage | None = None


def get_storage() -> DocumentStorage:
    """Get or create the global DocumentStorage instance."""
    global _storage
    if _storage is None:
        _storage = DocumentStorage()
    return _storage
