"""Document deduplication via SHA-256 file hash."""

import hashlib
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.document import Document


def compute_file_hash(data: bytes) -> str:
    """Return hex SHA-256 of raw file bytes."""
    return hashlib.sha256(data).hexdigest()


async def check_duplicate(
    db: AsyncSession,
    tenant_id: UUID,
    file_hash: str,
) -> Document | None:
    """Return existing Document if the same hash was already uploaded by this tenant.

    Looks only at completed or processing documents (not failed ones) so a
    re-upload after a failed processing attempt is allowed.
    """
    result = await db.execute(
        select(Document)
        .where(Document.tenant_id == tenant_id)
        .where(Document.file_hash == file_hash)
        .where(Document.status.in_(["pending", "processing", "completed"]))
        .limit(1)
    )
    return result.scalar_one_or_none()
