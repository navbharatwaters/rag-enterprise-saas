"""Batch embedding generation for document chunks.

Processes chunks in configurable batch sizes to balance
throughput and memory usage.
"""

import asyncio
import logging

from src.embeddings.client import EmbeddingsClient

logger = logging.getLogger(__name__)

EMBEDDING_BATCH_SIZE = 32
MAX_BATCH_RETRIES = 3


async def generate_embeddings_for_chunks(
    client: EmbeddingsClient,
    texts: list[str],
    batch_size: int = EMBEDDING_BATCH_SIZE,
    max_retries: int = MAX_BATCH_RETRIES,
) -> list[list[float]]:
    """Generate embeddings for a list of texts in batches.

    Each batch is independently retried with exponential backoff on failure,
    so a transient error in one batch does not fail the entire document.

    Args:
        client: EmbeddingsClient instance.
        texts: List of text strings to embed.
        batch_size: Number of texts per batch (default 32).
        max_retries: Per-batch retry attempts before raising (default 3).

    Returns:
        List of embedding vectors, same order as input texts.
    """
    if not texts:
        return []

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_end = min(i + batch_size, len(texts))
        logger.debug("Embedding batch %d-%d of %d", i, batch_end, len(texts))

        for attempt in range(1, max_retries + 1):
            try:
                embeddings = await client.embed(batch)
                all_embeddings.extend(embeddings)
                break
            except Exception as exc:
                if attempt == max_retries:
                    logger.error(
                        "Batch embedding failed after %d attempts (batch %d-%d): %s",
                        max_retries,
                        i,
                        batch_end,
                        exc,
                    )
                    raise
                wait = 2**attempt  # 2s, 4s, 8s
                logger.warning(
                    "Batch embedding attempt %d/%d failed (batch %d-%d), retrying in %ds: %s",
                    attempt,
                    max_retries,
                    i,
                    batch_end,
                    wait,
                    exc,
                )
                await asyncio.sleep(wait)

    return all_embeddings
