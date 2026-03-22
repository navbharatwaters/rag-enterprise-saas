"""FastAPI router for the generation/query API."""

import logging
import time

import httpx
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.auth.dependencies import CurrentTenant, CurrentUser, TenantDB
from src.billing.metering import record_usage
from src.billing.quotas import QuotaExceededError, enforce_query_quota
from src.billing.service import get_tenant_billing_info
from src.cache.dependencies import QueryCacheDep
from src.core.config import settings
from src.embeddings.client import EmbeddingsClient
from src.generation.context import ContextAssembler
from src.generation.llm import LLMClient, get_llm_client
from src.generation.reranker import RerankerClient, get_reranker
from src.generation.schemas import QueryRequest, QueryResponse
from src.generation.service import QueryService
from src.generation.streaming import stream_query_response
from src.retrieval.search import SearchService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["generation"])


def _build_query_service(db: object) -> QueryService:
    """Build QueryService with all dependencies."""
    embeddings = EmbeddingsClient()
    search_service = SearchService(db=db, embeddings_client=embeddings)

    try:
        reranker = get_reranker()
    except Exception:
        reranker = None

    context_assembler = ContextAssembler(
        max_context_tokens=settings.MAX_CONTEXT_TOKENS,
        reserved_for_answer=settings.RESERVED_ANSWER_TOKENS,
    )
    llm_client = get_llm_client()

    return QueryService(
        search_service=search_service,
        reranker=reranker,
        context_assembler=context_assembler,
        llm_client=llm_client,
    )


@router.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
    cache: QueryCacheDep,
):
    """Execute a RAG query against tenant documents.

    Non-streaming responses are cached in Redis for 1 hour. The response
    includes a `cached: true` field when served from cache. Streaming
    responses and conversation-scoped queries are never cached.

    Set `stream: true` in the request body for Server-Sent Events streaming.
    """
    # Enforce query quota
    try:
        tier, _stripe_cid = await get_tenant_billing_info(db, tenant_id)
        await enforce_query_quota(db, tenant_id, tier)
    except QuotaExceededError as e:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "message": f"Monthly query limit reached ({e.limit}). Upgrade your plan for more queries.",
                "quota_type": e.quota_type,
                "limit": e.limit,
                "current": e.current,
            },
        )

    tenant_str = str(tenant_id)
    user_str = str(user.user_id)

    if request.stream:
        # Streaming responses cannot be cached — no LLM response object to serialize.
        await _record_query_usage(db, tenant_id, _stripe_cid)

        embeddings = EmbeddingsClient()
        search_service = SearchService(db=db, embeddings_client=embeddings)

        try:
            reranker = get_reranker()
        except Exception:
            reranker = None

        context_assembler = ContextAssembler(
            max_context_tokens=settings.MAX_CONTEXT_TOKENS,
            reserved_for_answer=settings.RESERVED_ANSWER_TOKENS,
        )
        llm_client = get_llm_client()

        return EventSourceResponse(
            stream_query_response(
                request=request,
                tenant_id=tenant_str,
                user_id=user_str,
                search_service=search_service,
                reranker=reranker,
                context_assembler=context_assembler,
                llm_client=llm_client,
            )
        )

    # Non-streaming: attempt cache lookup.
    # Skip cache for conversation-scoped queries (history-dependent answers).
    filters_dict = (
        request.search_filters.model_dump(mode="json")
        if request.search_filters
        else None
    )
    use_cache = request.conversation_id is None

    if use_cache:
        cache_start = time.perf_counter()
        cached = await cache.get(tenant_str, request.question, filters_dict)
        if cached is not None:
            cached["cached"] = True
            cached["took_ms"] = round((time.perf_counter() - cache_start) * 1000, 1)
            await _record_query_usage(db, tenant_id, _stripe_cid)
            logger.debug("cache_hit tenant=%s question=%r", tenant_str, request.question[:80])
            return QueryResponse.model_validate(cached)

    # Cache miss — run the full RAG pipeline.
    try:
        service = _build_query_service(db)
        result = await service.query(request, tenant_str, user_str)
        await _record_query_usage(db, tenant_id, _stripe_cid)

        if use_cache:
            await cache.set(
                tenant_str,
                request.question,
                result.model_dump(mode="json"),
                filters_dict,
            )

        return result
    except httpx.ConnectError:
        logger.warning("Embeddings service unavailable for query")
        raise HTTPException(
            status_code=503,
            detail="Search service temporarily unavailable",
        )
    except Exception:
        logger.exception("Query failed")
        raise HTTPException(status_code=500, detail="Query processing failed")


async def _record_query_usage(db: object, tenant_id: object, stripe_customer_id: str | None) -> None:
    """Record query usage, logging failures without blocking."""
    try:
        await record_usage(
            db=db,
            tenant_id=tenant_id,
            stripe_customer_id=stripe_customer_id,
            event_name="query_executed",
        )
    except Exception:
        logger.warning("Failed to record query usage for tenant %s", tenant_id, exc_info=True)
