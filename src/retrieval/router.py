"""Search API router.

Provides POST /api/v1/search endpoint with authentication,
tenant isolation, and graceful fallback to BM25 when embeddings
service is unavailable.
"""

import asyncio
import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.auth.dependencies import CurrentUser, CurrentTenant, TenantDB
from src.embeddings.client import EmbeddingsClient
from src.retrieval.analytics import build_analytics_from_response, log_search
from src.retrieval.query_decomposer import query_decomposer
from src.retrieval.schemas import SearchMode, SearchRequest, SearchResponse
from src.retrieval.search import SearchService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    *,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
):
    """Execute a search query against the tenant's documents.

    Supports three modes:
    - **hybrid**: Combines BM25 keyword + vector semantic search (default)
    - **bm25**: Keyword search only
    - **vector**: Semantic search only

    If the embeddings service is unavailable during hybrid or vector search,
    the endpoint gracefully falls back to BM25-only search.
    """
    embeddings_client = EmbeddingsClient()
    service = SearchService(db=db, embeddings_client=embeddings_client)

    try:
        response = await service.search(request)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        # Embeddings service unavailable — fallback to BM25
        if request.mode in (SearchMode.HYBRID, SearchMode.VECTOR_ONLY):
            logger.warning(
                "Embeddings service unavailable, falling back to BM25: %s", exc
            )
            fallback_request = request.model_copy(update={"mode": SearchMode.BM25_ONLY})
            response = await service.search(fallback_request)
        else:
            raise
    except Exception as exc:
        logger.error("Search failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed")

    # Fire-and-forget analytics logging
    analytics_kwargs = build_analytics_from_response(
        response,
        tenant_id=tenant_id,
        user_id=user.user_id,
        filters=request.filters.model_dump() if request.filters else None,
    )
    asyncio.create_task(log_search(**analytics_kwargs))

    return response


class DecomposeRequest(BaseModel):
    question: str


@router.post("/search/decompose")
async def decompose_query(
    request: DecomposeRequest,
    user: CurrentUser,
):
    """Debug endpoint: show how a question would be decomposed into sub-queries."""
    return await query_decomposer.decompose_with_reasoning(request.question)
