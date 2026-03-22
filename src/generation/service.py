"""Query service orchestrating the full RAG generation pipeline."""

import logging
import time
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.generation.citations import filter_used_sources, validate_citations
from src.generation.context import ContextAssembler
from src.generation.llm import LLMClient, LLMResponse
from src.generation.prompts import NO_RESULTS_RESPONSE, build_messages
from src.generation.reranker import RerankerClient
from src.generation.schemas import (
    CitationStyle,
    QueryRequest,
    QueryResponse,
    Source,
    TokenUsage,
)
from src.retrieval.multi_query import multi_query_retriever
from src.retrieval.schemas import SearchMode, SearchRequest, SearchResult
from src.retrieval.search import RRF_K, SearchService

# Max possible RRF score: a result ranked 1st in both BM25 and vector.
_MAX_HYBRID_SCORE = 2.0 / (RRF_K + 1)

logger = logging.getLogger(__name__)


class QueryService:
    """Orchestrate the full RAG pipeline: search → rerank → context → LLM → citations."""

    def __init__(
        self,
        search_service: SearchService,
        reranker: RerankerClient | None,
        context_assembler: ContextAssembler,
        llm_client: LLMClient,
    ):
        self.search = search_service
        self.reranker = reranker
        self.context_assembler = context_assembler
        self.llm = llm_client

    async def query(
        self,
        request: QueryRequest,
        tenant_id: str,
        user_id: str,
    ) -> QueryResponse:
        """Execute the full RAG query pipeline (non-streaming).

        Pipeline: search → rerank → context → LLM → citations → response
        """
        start = time.perf_counter()
        query_id = uuid4()

        # 1. Retrieve (with optional query decomposition)
        search_results, sub_queries, was_decomposed = await self._retrieve(request)

        # 2. Handle no results
        if not search_results:
            return self._no_results_response(query_id, request, sub_queries, was_decomposed)

        # 3. Rerank (optional)
        reranked = await self._rerank(request.question, search_results)

        # 4. Assemble context
        context, sources = self.context_assembler.assemble(
            reranked, request.question
        )

        # 5. Build prompt
        messages = build_messages(
            question=request.question,
            context=context,
        )

        # 6. Generate LLM response
        llm_response = await self.llm.generate(
            messages=messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=False,
            metadata={"tenant_id": tenant_id, "user_id": user_id},
        )
        assert isinstance(llm_response, LLMResponse)

        # 7. Process citations
        answer = llm_response.content
        if request.include_citations and request.citation_style != CitationStyle.NONE:
            answer, valid_ids = validate_citations(answer, sources)
            sources = filter_used_sources(sources, valid_ids)

        took_ms = (time.perf_counter() - start) * 1000

        return QueryResponse(
            answer=answer,
            sources=sources,
            query_id=query_id,
            conversation_id=request.conversation_id,
            model=llm_response.model,
            usage=llm_response.usage,
            took_ms=round(took_ms, 1),
            confidence_score=_compute_confidence(sources),
            was_decomposed=was_decomposed,
            sub_queries=sub_queries,
        )

    async def query_with_history(
        self,
        request: QueryRequest,
        tenant_id: str,
        user_id: str,
        history: list[dict[str, str]],
    ) -> QueryResponse:
        """Execute RAG query with conversation history."""
        start = time.perf_counter()
        query_id = uuid4()

        search_results, sub_queries, was_decomposed = await self._retrieve(request)

        if not search_results:
            return self._no_results_response(query_id, request, sub_queries, was_decomposed)

        reranked = await self._rerank(request.question, search_results)
        context, sources = self.context_assembler.assemble(
            reranked, request.question
        )

        messages = build_messages(
            question=request.question,
            context=context,
            history=history,
        )

        llm_response = await self.llm.generate(
            messages=messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=False,
            metadata={"tenant_id": tenant_id, "user_id": user_id},
        )
        assert isinstance(llm_response, LLMResponse)

        answer = llm_response.content
        if request.include_citations and request.citation_style != CitationStyle.NONE:
            answer, valid_ids = validate_citations(answer, sources)
            sources = filter_used_sources(sources, valid_ids)

        took_ms = (time.perf_counter() - start) * 1000

        return QueryResponse(
            answer=answer,
            sources=sources,
            query_id=query_id,
            conversation_id=request.conversation_id,
            model=llm_response.model,
            usage=llm_response.usage,
            took_ms=round(took_ms, 1),
            confidence_score=_compute_confidence(sources),
            was_decomposed=was_decomposed,
            sub_queries=sub_queries,
        )

    async def _retrieve(
        self, request: QueryRequest
    ) -> tuple[list[SearchResult], list[str], bool]:
        """Retrieve relevant chunks, optionally via query decomposition.

        Returns:
            (search_results, sub_queries, was_decomposed)
        """
        if settings.ENABLE_QUERY_DECOMPOSITION:
            retrieval = await multi_query_retriever.retrieve(
                question=request.question,
                search_service=self.search,
                top_k=request.max_sources * 4,
                filters=request.search_filters,
                search_mode=SearchMode.HYBRID,
            )
            return (
                retrieval["results"],
                retrieval["sub_queries"],
                retrieval["was_decomposed"],
            )

        # Decomposition disabled — plain single-query search
        min_score: float | None = None
        if request.min_relevance_score is not None:
            min_score = request.min_relevance_score * _MAX_HYBRID_SCORE

        search_request = SearchRequest(
            query=request.question,
            mode=SearchMode.HYBRID,
            limit=request.max_sources * 4,
            filters=request.search_filters,
            min_relevance_score=min_score,
        )
        response = await self.search.search(search_request)
        return response.results, [request.question], False

    async def _rerank(
        self, query: str, results: list[SearchResult]
    ) -> list[SearchResult]:
        """Rerank results if reranker is available."""
        if not self.reranker or not results:
            return results

        try:
            passages = [r.content or "" for r in results]
            reranked = await self.reranker.rerank(query, passages)

            # Reorder results by rerank score
            reordered = []
            for rr in reranked:
                if rr.index < len(results):
                    reordered.append(results[rr.index])
            return reordered
        except Exception:
            logger.warning("Reranker failed, falling back to search order", exc_info=True)
            return results

    def _no_results_response(
        self,
        query_id: object,
        request: QueryRequest,
        sub_queries: list[str] | None = None,
        was_decomposed: bool = False,
    ) -> QueryResponse:
        """Build response when no search results found."""
        return QueryResponse(
            answer=NO_RESULTS_RESPONSE,
            sources=[],
            query_id=query_id,  # type: ignore[arg-type]
            conversation_id=request.conversation_id,
            model=request.model or self.llm.default_model,
            usage=TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            took_ms=0.0,
            confidence_score=0.0,
            was_decomposed=was_decomposed,
            sub_queries=sub_queries or [request.question],
        )


def _compute_confidence(sources: list[Source]) -> float:
    """Compute a normalized [0, 1] confidence score from included sources.

    Uses the mean relevance score of sources normalized against the maximum
    possible hybrid RRF score (both methods rank 1). Returns 0.0 when no
    sources are available.
    """
    if not sources:
        return 0.0
    mean_score = sum(s.relevance_score for s in sources) / len(sources)
    return round(min(1.0, mean_score / _MAX_HYBRID_SCORE), 3)
