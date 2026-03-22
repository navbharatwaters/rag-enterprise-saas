"""Batch document processing — track multi-file upload jobs."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class BatchStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"       # some succeeded, some failed
    FAILED = "failed"


@dataclass
class BatchJob:
    id: UUID
    tenant_id: str
    status: BatchStatus
    total_documents: int
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    document_ids: list[UUID] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


class BatchProcessor:
    """Handle batch document uploads and track their status via the DB."""

    MAX_BATCH_SIZE = 20

    async def create_batch(
        self,
        db: AsyncSession,
        tenant_id: str,
        document_ids: list[UUID],
    ) -> BatchJob:
        """Persist a new batch job and return it."""
        if len(document_ids) > self.MAX_BATCH_SIZE:
            raise ValueError(f"Batch size exceeds maximum of {self.MAX_BATCH_SIZE}")

        batch = BatchJob(
            id=uuid4(),
            tenant_id=tenant_id,
            status=BatchStatus.PENDING,
            total_documents=len(document_ids),
            document_ids=document_ids,
        )

        await db.execute(
            text("""
                INSERT INTO batch_jobs
                    (id, tenant_id, status, total_documents, document_ids, created_at)
                VALUES
                    (:id, :tenant_id, :status, :total, :doc_ids, :created_at)
            """),
            {
                "id": str(batch.id),
                "tenant_id": tenant_id,
                "status": batch.status.value,
                "total": batch.total_documents,
                "doc_ids": [str(d) for d in document_ids],
                "created_at": batch.created_at,
            },
        )
        logger.info("Created batch %s with %d documents", batch.id, len(document_ids))
        return batch

    async def get_batch_status(
        self,
        db: AsyncSession,
        batch_id: UUID,
        tenant_id: str,
    ) -> dict | None:
        """Return live batch status by querying document rows."""
        # Fetch batch record
        batch_result = await db.execute(
            text("""
                SELECT id, tenant_id, status, total_documents, document_ids, created_at, completed_at
                FROM batch_jobs
                WHERE id = :id AND tenant_id = :tenant_id
            """),
            {"id": str(batch_id), "tenant_id": tenant_id},
        )
        row = batch_result.fetchone()
        if row is None:
            return None

        doc_ids = row[4]  # UUID[]

        # Query live document statuses
        if doc_ids:
            docs_result = await db.execute(
                text("""
                    SELECT status, COUNT(*) AS cnt
                    FROM documents
                    WHERE id = ANY(:ids::uuid[])
                    GROUP BY status
                """),
                {"ids": doc_ids},
            )
            status_counts: dict[str, int] = {r[0]: r[1] for r in docs_result}
        else:
            status_counts = {}

        succeeded = status_counts.get("completed", 0)
        failed = status_counts.get("failed", 0)
        processing = status_counts.get("processing", 0)
        pending = status_counts.get("pending", 0)
        processed = succeeded + failed

        total = row[3]
        if total > 0:
            progress_pct = round(processed / total * 100, 1)
        else:
            progress_pct = 0.0

        # Derive aggregate status
        if processed == 0:
            agg_status = BatchStatus.PENDING.value if pending + processing > 0 else BatchStatus.PROCESSING.value
        elif processed < total:
            agg_status = BatchStatus.PROCESSING.value
        elif failed == 0:
            agg_status = BatchStatus.COMPLETED.value
        elif succeeded == 0:
            agg_status = BatchStatus.FAILED.value
        else:
            agg_status = BatchStatus.PARTIAL.value

        return {
            "id": str(row[0]),
            "status": agg_status,
            "total": total,
            "processed": processed,
            "succeeded": succeeded,
            "failed": failed,
            "pending": pending + processing,
            "progress_pct": progress_pct,
            "created_at": row[5].isoformat() if row[5] else None,
            "completed_at": row[6].isoformat() if row[6] else None,
        }


batch_processor = BatchProcessor()
