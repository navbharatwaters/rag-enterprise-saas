"""Embedding cache with database persistence.

Cache key is SHA-256(text) + model_version.
The embedding_cache table has no RLS (shared across tenants)
since embeddings are content-addressable and tenant-independent.
"""

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.embeddings.client import EmbeddingsClient
from src.models.embedding_cache import EmbeddingCache

logger = logging.getLogger(__name__)


def _text_hash(text: str) -> str:
    """Generate deterministic cache key from text content."""
    return hashlib.sha256(text.encode()).hexdigest()


async def get_cached_embedding(
    db: AsyncSession,
    text: str,
    model_version: str,
) -> list[float] | None:
    """Look up an embedding in the cache.

    Returns the embedding vector if found, None otherwise.
    Updates last_accessed_at on hit.
    """
    text_hash = _text_hash(text)
    result = await db.execute(
        select(EmbeddingCache).where(
            EmbeddingCache.text_hash == text_hash,
            EmbeddingCache.model_version == model_version,
        )
    )
    cached = result.scalar_one_or_none()

    if cached is None:
        return None

    # Update last_accessed_at
    await db.execute(
        update(EmbeddingCache)
        .where(EmbeddingCache.text_hash == text_hash)
        .values(last_accessed_at=datetime.now(timezone.utc))
    )

    return cached.embedding.tolist() if hasattr(cached.embedding, "tolist") else list(cached.embedding)


async def store_cached_embedding(
    db: AsyncSession,
    text: str,
    model_version: str,
    embedding: list[float],
) -> None:
    """Store an embedding in the cache."""
    text_hash = _text_hash(text)
    entry = EmbeddingCache(
        text_hash=text_hash,
        model_version=model_version,
        embedding=embedding,
    )
    await db.merge(entry)  # Upsert: update if exists, insert if not


async def get_or_create_embedding(
    db: AsyncSession,
    client: EmbeddingsClient,
    text: str,
) -> list[float]:
    """Get embedding from cache or generate and cache it.

    Args:
        db: Database session (admin, no RLS needed for cache table).
        client: EmbeddingsClient for generating new embeddings.
        text: Text to embed.

    Returns:
        Embedding vector (1024 floats).
    """
    cached = await get_cached_embedding(db, text, client.model)
    if cached is not None:
        logger.debug("cache_hit text_hash=%s", _text_hash(text)[:8])
        return cached

    logger.debug("cache_miss text_hash=%s", _text_hash(text)[:8])
    embedding = await client.embed_single(text)

    await store_cached_embedding(db, text, client.model, embedding)

    return embedding
