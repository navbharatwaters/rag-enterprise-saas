"""Incremental re-indexing — only update changed chunks."""

import hashlib
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.documents.chunker import ChunkData

logger = logging.getLogger(__name__)


class IncrementalIndexer:
    """Compute diffs between new chunks and what is already stored for a document."""

    @staticmethod
    def hash_chunk(content: str) -> str:
        """SHA-256 of chunk content (matches existing content_hash column)."""
        return hashlib.sha256(content.encode()).hexdigest()

    async def get_existing_chunks(
        self,
        db: AsyncSession,
        document_id: str,
    ) -> dict[str, dict]:
        """Return {content_hash: {id, chunk_index}} for all stored chunks."""
        result = await db.execute(
            text("""
                SELECT id, chunk_index, content_hash
                FROM chunks
                WHERE document_id = :doc_id
            """),
            {"doc_id": document_id},
        )
        return {
            row[2]: {"id": str(row[0]), "chunk_index": row[1]}
            for row in result
        }

    async def compute_diff(
        self,
        db: AsyncSession,
        document_id: str,
        new_chunks: list[ChunkData],
    ) -> dict:
        """Compare new ChunkData list against stored chunks.

        Returns:
            {
                "to_add":    list[ChunkData],   # chunks not yet in DB
                "to_delete": list[str],          # chunk UUIDs to remove
                "unchanged": list[str],          # chunk UUIDs kept as-is
                "stats":     dict,
            }
        """
        existing = await self.get_existing_chunks(db, document_id)
        existing_hashes = set(existing.keys())

        new_hash_map: dict[str, ChunkData] = {
            self.hash_chunk(c.content): c for c in new_chunks
        }
        new_hashes = set(new_hash_map.keys())

        to_add = [new_hash_map[h] for h in (new_hashes - existing_hashes)]
        to_delete = [existing[h]["id"] for h in (existing_hashes - new_hashes)]
        unchanged = [existing[h]["id"] for h in (existing_hashes & new_hashes)]

        stats = {
            "added": len(to_add),
            "deleted": len(to_delete),
            "unchanged": len(unchanged),
            "total_new": len(new_chunks),
            "total_old": len(existing),
        }
        logger.info("Incremental diff for doc %s: %s", document_id, stats)
        return {
            "to_add": to_add,
            "to_delete": to_delete,
            "unchanged": unchanged,
            "stats": stats,
        }

    async def delete_chunks(self, db: AsyncSession, chunk_ids: list[str]) -> None:
        """Delete chunks by UUID list."""
        if not chunk_ids:
            return
        await db.execute(
            text("DELETE FROM chunks WHERE id = ANY(:ids::uuid[])"),
            {"ids": chunk_ids},
        )
        logger.info("Deleted %d stale chunks", len(chunk_ids))


incremental_indexer = IncrementalIndexer()
