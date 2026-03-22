"""Core search service implementing hybrid, BM25, and vector search.

Uses PostgreSQL pg_search (@@@ operator) for BM25 and pgvector (<=> operator)
for cosine similarity. Hybrid mode combines both with Reciprocal Rank Fusion.
"""

import logging
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.embeddings.client import EmbeddingsClient
from src.retrieval.filters import build_filter_clauses
from src.retrieval.highlighting import extract_query_terms, highlight_content
from src.retrieval.schemas import (
    SearchFilters,
    SearchMode,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

logger = logging.getLogger(__name__)

# RRF ranking constant — k=60 is the standard default from research
RRF_K = 60

# Number of candidates from each retrieval method before fusion
CANDIDATE_LIMIT = 50


class SearchService:
    """Executes search queries against the chunks table.

    Requires an AsyncSession with RLS tenant context already set
    and an EmbeddingsClient for vector search.
    """

    def __init__(self, db: AsyncSession, embeddings_client: EmbeddingsClient):
        self.db = db
        self.embeddings_client = embeddings_client

    async def search(self, request: SearchRequest) -> SearchResponse:
        """Execute a search based on the request mode.

        Routes to the appropriate search implementation and adds
        highlighting to the results.
        """
        start = time.perf_counter()

        if request.mode == SearchMode.HYBRID:
            results, total = await self._hybrid_search(request)
        elif request.mode == SearchMode.BM25_ONLY:
            results, total = await self._bm25_search(request)
        elif request.mode == SearchMode.VECTOR_ONLY:
            results, total = await self._vector_search(request)
        else:
            results, total = [], 0

        # Filter by relevance threshold
        if request.min_relevance_score is not None:
            results = [r for r in results if r.score >= request.min_relevance_score]
            total = len(results) + request.offset

        # Add highlighting
        if request.highlight and results:
            query_terms = extract_query_terms(request.query)
            for result in results:
                if result.content:
                    result.highlights = highlight_content(
                        result.content,
                        query_terms,
                        max_length=request.highlight_max_length,
                    )

        # Strip content if not requested
        if not request.include_content:
            for result in results:
                result.content = None

        took_ms = (time.perf_counter() - start) * 1000

        return SearchResponse(
            results=results,
            total=total,
            query=request.query,
            mode=request.mode,
            took_ms=round(took_ms, 2),
        )

    async def _hybrid_search(
        self, request: SearchRequest
    ) -> tuple[list[SearchResult], int]:
        """Hybrid search combining BM25 + vector with RRF fusion."""
        # Generate query embedding
        query_embedding = await self.embeddings_client.embed_single(request.query, task="retrieval.query")

        filter_clauses, filter_params = build_filter_clauses(request.filters)
        filter_sql = _build_filter_sql(filter_clauses)

        sql = text(f"""
            WITH
            bm25_results AS (
                SELECT
                    c.id,
                    c.document_id,
                    c.content,
                    c.chunk_index,
                    c.page_number,
                    c.metadata,
                    c.token_count,
                    ROW_NUMBER() OVER (ORDER BY paradedb.score(c.id) DESC) AS bm25_rank
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.content @@@ :query_text
                {filter_sql}
                LIMIT :candidate_limit
            ),
            vector_results AS (
                SELECT
                    c.id,
                    c.document_id,
                    c.content,
                    c.chunk_index,
                    c.page_number,
                    c.metadata,
                    c.token_count,
                    ROW_NUMBER() OVER (ORDER BY c.embedding <=> :query_embedding) AS vector_rank
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL
                {filter_sql}
                ORDER BY c.embedding <=> :query_embedding
                LIMIT :candidate_limit
            ),
            rrf_scores AS (
                SELECT
                    id,
                    SUM(score) AS rrf_score,
                    MIN(bm25_rank) AS bm25_rank,
                    MIN(vector_rank) AS vector_rank
                FROM (
                    SELECT id, 1.0 / (:rrf_k + bm25_rank) AS score,
                           bm25_rank, NULL::int AS vector_rank
                    FROM bm25_results
                    UNION ALL
                    SELECT id, 1.0 / (:rrf_k + vector_rank) AS score,
                           NULL::int AS bm25_rank, vector_rank
                    FROM vector_results
                ) combined
                GROUP BY id
            )
            SELECT
                c.id AS chunk_id,
                c.content,
                c.document_id,
                c.chunk_index,
                c.page_number,
                c.metadata,
                d.filename,
                d.file_type,
                COALESCE(d.title, d.filename) AS document_title,
                r.rrf_score AS score,
                r.bm25_rank,
                r.vector_rank
            FROM rrf_scores r
            JOIN chunks c ON c.id = r.id
            JOIN documents d ON d.id = c.document_id
            ORDER BY r.rrf_score DESC
            LIMIT :result_limit
            OFFSET :result_offset
        """)

        params = {
            "query_text": request.query,
            "query_embedding": _format_embedding(query_embedding),
            "candidate_limit": CANDIDATE_LIMIT,
            "rrf_k": RRF_K,
            "result_limit": request.limit,
            "result_offset": request.offset,
            **filter_params,
        }

        result = await self.db.execute(sql, params)
        rows = result.mappings().all()

        results = [_row_to_search_result(row) for row in rows]

        # Estimate total (count of unique chunks across both methods)
        total = len(results) + request.offset
        if len(results) == request.limit:
            # There may be more results
            total = max(total, request.offset + request.limit + 1)

        return results, total

    async def _bm25_search(
        self, request: SearchRequest
    ) -> tuple[list[SearchResult], int]:
        """BM25-only keyword search using pg_search @@@ operator."""
        filter_clauses, filter_params = build_filter_clauses(request.filters)
        filter_sql = _build_filter_sql(filter_clauses)

        sql = text(f"""
            SELECT
                c.id AS chunk_id,
                c.content,
                c.document_id,
                c.chunk_index,
                c.page_number,
                c.metadata,
                d.filename,
                d.file_type,
                COALESCE(d.title, d.filename) AS document_title,
                paradedb.score(c.id) AS score,
                ROW_NUMBER() OVER (ORDER BY paradedb.score(c.id) DESC) AS bm25_rank,
                NULL::int AS vector_rank
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.content @@@ :query_text
            {filter_sql}
            ORDER BY paradedb.score(c.id) DESC
            LIMIT :result_limit
            OFFSET :result_offset
        """)

        params = {
            "query_text": request.query,
            "result_limit": request.limit,
            "result_offset": request.offset,
            **filter_params,
        }

        result = await self.db.execute(sql, params)
        rows = result.mappings().all()

        results = [_row_to_search_result(row) for row in rows]
        total = len(results) + request.offset
        if len(results) == request.limit:
            total = max(total, request.offset + request.limit + 1)

        return results, total

    async def _vector_search(
        self, request: SearchRequest
    ) -> tuple[list[SearchResult], int]:
        """Vector-only semantic search using pgvector <=> operator."""
        query_embedding = await self.embeddings_client.embed_single(request.query, task="retrieval.query")

        filter_clauses, filter_params = build_filter_clauses(request.filters)
        filter_sql = _build_filter_sql(filter_clauses)

        sql = text(f"""
            SELECT
                c.id AS chunk_id,
                c.content,
                c.document_id,
                c.chunk_index,
                c.page_number,
                c.metadata,
                d.filename,
                d.file_type,
                COALESCE(d.title, d.filename) AS document_title,
                1 - (c.embedding <=> :query_embedding) AS score,
                NULL::int AS bm25_rank,
                ROW_NUMBER() OVER (ORDER BY c.embedding <=> :query_embedding) AS vector_rank
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.embedding IS NOT NULL
            {filter_sql}
            ORDER BY c.embedding <=> :query_embedding
            LIMIT :result_limit
            OFFSET :result_offset
        """)

        params = {
            "query_embedding": _format_embedding(query_embedding),
            "result_limit": request.limit,
            "result_offset": request.offset,
            **filter_params,
        }

        result = await self.db.execute(sql, params)
        rows = result.mappings().all()

        results = [_row_to_search_result(row) for row in rows]
        total = len(results) + request.offset
        if len(results) == request.limit:
            total = max(total, request.offset + request.limit + 1)

        return results, total


def _row_to_search_result(row) -> SearchResult:
    """Convert a database row mapping to a SearchResult."""
    return SearchResult(
        chunk_id=row["chunk_id"],
        content=row["content"],
        document_id=row["document_id"],
        document_title=row["document_title"],
        filename=row["filename"],
        file_type=row["file_type"],
        chunk_index=row["chunk_index"],
        page_number=row["page_number"],
        score=float(row["score"]),
        bm25_rank=row["bm25_rank"],
        vector_rank=row["vector_rank"],
        metadata=row["metadata"] if row["metadata"] else {},
    )


def _build_filter_sql(clauses) -> str:
    """Join filter clauses into a SQL fragment with AND prefix."""
    if not clauses:
        return ""
    parts = [str(c) for c in clauses]
    return "AND " + " AND ".join(parts)


def _format_embedding(embedding: list[float]) -> str:
    """Format embedding vector as PostgreSQL vector literal."""
    return "[" + ",".join(str(v) for v in embedding) + "]"
