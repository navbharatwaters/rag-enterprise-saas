"""Embeddings client supporting self-hosted BGE-M3 and Jina AI API.

Supports batch embedding with retries for transient failures.
Provider is selected via EMBEDDINGS_PROVIDER setting:
  - "self-hosted": calls local HTTP service at EMBEDDINGS_URL
  - "jina-v4": calls Jina AI API using JINA_API_KEY
"""

import asyncio
import logging

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)

# Target output dimensions — must match VECTOR(dim=1024) in the database schema
EMBEDDING_DIMENSIONS = 1024


class EmbeddingsClient:
    """Async embeddings client supporting self-hosted BGE-M3 or Jina AI."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
    ):
        self.base_url = (base_url or settings.EMBEDDINGS_URL).rstrip("/")
        self.model = model or settings.EMBEDDINGS_MODEL
        self.timeout = timeout
        self.max_retries = max_retries
        self.dimensions = EMBEDDING_DIMENSIONS
        self._provider = settings.EMBEDDINGS_PROVIDER

    async def embed(
        self, texts: list[str], task: str = "retrieval.passage"
    ) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Args:
            texts: List of strings to embed (max 256 per call).
            task: Jina task hint — "retrieval.passage" for documents,
                  "retrieval.query" for search queries. Ignored for self-hosted.

        Returns:
            List of embedding vectors (1024 dimensions each).

        Raises:
            httpx.HTTPStatusError: On non-retryable HTTP errors.
            httpx.ConnectError / httpx.TimeoutException: After all retries.
        """
        if not texts:
            return []

        if self._provider == "jina-v4":
            return await self._embed_jina(texts, task)
        return await self._embed_self_hosted(texts)

    async def _embed_jina(
        self, texts: list[str], task: str
    ) -> list[list[float]]:
        """Call Jina AI embeddings API."""
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        "https://api.jina.ai/v1/embeddings",
                        headers={
                            "Authorization": f"Bearer {settings.JINA_API_KEY}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.model,
                            "input": texts,
                            "dimensions": EMBEDDING_DIMENSIONS,
                            "task": task,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                # Jina returns {"data": [{"index": i, "embedding": [...]}]}
                items = sorted(data["data"], key=lambda x: x["index"])
                return [item["embedding"] for item in items]

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                logger.warning(
                    "embed_jina_retry attempt=%d/%d error=%s",
                    attempt,
                    self.max_retries,
                    e,
                )
                if attempt == self.max_retries:
                    raise
                await asyncio.sleep(2 ** (attempt - 1))
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < self.max_retries:
                    last_error = e
                    logger.warning(
                        "embed_jina_server_error attempt=%d/%d status=%d, retrying",
                        attempt,
                        self.max_retries,
                        e.response.status_code,
                    )
                    await asyncio.sleep(2 ** (attempt - 1))
                    continue
                raise

        raise last_error  # unreachable

    async def _embed_self_hosted(self, texts: list[str]) -> list[list[float]]:
        """Call self-hosted BGE-M3 service."""
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/embed",
                        json={"texts": texts, "model": self.model},
                    )
                    response.raise_for_status()
                    result = response.json()
                return result["embeddings"]

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                logger.warning(
                    "embed_retry attempt=%d/%d error=%s",
                    attempt,
                    self.max_retries,
                    e,
                )
                if attempt == self.max_retries:
                    raise
                await asyncio.sleep(2 ** (attempt - 1))
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < self.max_retries:
                    last_error = e
                    logger.warning(
                        "embed_server_error attempt=%d/%d status=%d, retrying",
                        attempt,
                        self.max_retries,
                        e.response.status_code,
                    )
                    await asyncio.sleep(2 ** (attempt - 1))
                    continue
                raise

        raise last_error  # unreachable

    async def embed_single(
        self, text: str, task: str = "retrieval.passage"
    ) -> list[float]:
        """Embed a single text string."""
        embeddings = await self.embed([text], task=task)
        return embeddings[0]

    async def health(self) -> dict:
        """Check embeddings service health."""
        if self._provider == "jina-v4":
            # Verify Jina key with a minimal request
            result = await self._embed_jina(["health check"], "retrieval.passage")
            return {"status": "ok", "provider": "jina-v4", "dims": len(result[0])}
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()
