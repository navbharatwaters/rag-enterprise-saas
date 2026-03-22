"""Reranker client supporting self-hosted BGE-reranker and Jina AI API.

Provider is selected via RERANKER_PROVIDER setting:
  - "self-hosted": calls local HTTP service at RERANKER_URL
  - "jina": calls Jina AI reranking API using JINA_API_KEY
"""

import logging

import httpx
from pydantic import BaseModel

from src.core.config import settings

logger = logging.getLogger(__name__)


class RerankResult(BaseModel):
    """Single rerank result."""

    index: int
    score: float


class RerankerClient:
    """Reranker client supporting self-hosted BGE or Jina AI."""

    def __init__(
        self,
        base_url: str = "",
        timeout: float = 30.0,
    ):
        self.base_url = base_url or settings.RERANKER_URL
        self.timeout = timeout
        self._provider = settings.RERANKER_PROVIDER
        self._model = settings.RERANKER_MODEL
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    async def rerank(
        self,
        query: str,
        passages: list[str],
        top_k: int = 10,
    ) -> list[RerankResult]:
        """Rerank passages by relevance to query.

        Args:
            query: The search query
            passages: List of text passages to rerank
            top_k: Number of top results to return

        Returns:
            List of RerankResult sorted by score descending
        """
        if not passages:
            return []

        if self._provider == "jina":
            return await self._rerank_jina(query, passages, top_k)
        return await self._rerank_self_hosted(query, passages, top_k)

    async def _rerank_jina(
        self, query: str, passages: list[str], top_k: int
    ) -> list[RerankResult]:
        """Call Jina AI reranking API."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://api.jina.ai/v1/rerank",
                headers={
                    "Authorization": f"Bearer {settings.JINA_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "query": query,
                    "documents": passages,
                    "top_n": top_k,
                },
            )
            response.raise_for_status()
            data = response.json()

        # Jina returns {"results": [{"index": i, "relevance_score": f}]}
        results = [
            RerankResult(index=item["index"], score=item["relevance_score"])
            for item in data["results"]
        ]
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    async def _rerank_self_hosted(
        self, query: str, passages: list[str], top_k: int
    ) -> list[RerankResult]:
        """Call self-hosted BGE reranker service."""
        client = await self._get_client()

        response = await client.post(
            "/rerank",
            json={
                "query": query,
                "texts": passages,
                "truncate": True,
            },
        )
        response.raise_for_status()

        data = response.json()
        results = [
            RerankResult(index=item["index"], score=item["score"])
            for item in data
        ]
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


_reranker: RerankerClient | None = None


def get_reranker() -> RerankerClient:
    """Get singleton RerankerClient instance."""
    global _reranker
    if _reranker is None:
        _reranker = RerankerClient()
    return _reranker
