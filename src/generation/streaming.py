"""SSE streaming helpers for RAG generation pipeline."""

import json
import logging
import time
from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

from src.generation.context import ContextAssembler
from src.generation.llm import LLMClient
from src.generation.prompts import NO_RESULTS_RESPONSE, build_messages
from src.generation.reranker import RerankerClient
from src.generation.schemas import QueryRequest, Source
from src.retrieval.schemas import SearchMode, SearchRequest, SearchResult
from src.retrieval.search import SearchService

logger = logging.getLogger(__name__)


def _sse_event(event: str, data: dict | str) -> str:
    """Format a single SSE event."""
    if isinstance(data, dict):
        data = json.dumps(data)
    return f"event: {event}\ndata: {data}\n\n"


async def stream_query_response(
    request: QueryRequest,
    tenant_id: str,
    user_id: str,
    search_service: SearchService,
    reranker: RerankerClient | None,
    context_assembler: ContextAssembler,
    llm_client: LLMClient,
    history: list[dict[str, str]] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream RAG query response as SSE events.

    Events emitted:
        start: {"query_id": ..., "model": ...}
        sources: {"sources": [...]}
        token: {"content": "..."}
        usage: {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}
        done: {"took_ms": ...}
        error: {"message": "..."}
    """
    start = time.perf_counter()
    query_id = uuid4()
    model = request.model or llm_client.default_model

    try:
        # Emit start event
        yield _sse_event("start", {
            "query_id": str(query_id),
            "model": model,
        })

        # 1. Search
        search_request = SearchRequest(
            query=request.question,
            mode=SearchMode.HYBRID,
            limit=request.max_sources * 4,
            filters=request.search_filters,
        )
        search_response = await search_service.search(search_request)
        results = search_response.results

        # 2. Handle no results
        if not results:
            yield _sse_event("token", {"content": NO_RESULTS_RESPONSE})
            took_ms = (time.perf_counter() - start) * 1000
            yield _sse_event("done", {"took_ms": round(took_ms, 1)})
            return

        # 3. Rerank
        reranked = await _rerank(reranker, request.question, results)

        # 4. Context assembly
        context, sources = context_assembler.assemble(reranked, request.question)

        # 5. Emit sources before tokens
        yield _sse_event("sources", {
            "sources": [s.model_dump(mode="json") for s in sources],
        })

        # 6. Build prompt
        messages = build_messages(
            question=request.question,
            context=context,
            history=history,
        )

        # 7. Stream LLM response
        stream = await llm_client.generate(
            messages=messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=True,
            metadata={"tenant_id": tenant_id, "user_id": user_id},
        )

        async for token in stream:
            yield _sse_event("token", {"content": token})

        # 8. Done
        took_ms = (time.perf_counter() - start) * 1000
        yield _sse_event("done", {"took_ms": round(took_ms, 1)})

    except Exception as e:
        logger.exception("Error during streaming query")
        yield _sse_event("error", {"message": str(e)})


async def _rerank(
    reranker: RerankerClient | None,
    query: str,
    results: list[SearchResult],
) -> list[SearchResult]:
    """Rerank results, falling back to original order on failure."""
    if not reranker or not results:
        return results

    try:
        passages = [r.content or "" for r in results]
        reranked = await reranker.rerank(query, passages)
        reordered = []
        for rr in reranked:
            if rr.index < len(results):
                reordered.append(results[rr.index])
        return reordered
    except Exception:
        logger.warning("Reranker failed, falling back to search order", exc_info=True)
        return results
