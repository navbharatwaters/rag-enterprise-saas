"""Multi-query retrieval: search with decomposed sub-queries and merge results."""

import asyncio
import logging
from collections import defaultdict

from src.retrieval.query_decomposer import query_decomposer
from src.retrieval.schemas import SearchFilters, SearchMode, SearchRequest, SearchResult

logger = logging.getLogger(__name__)


class MultiQueryRetriever:
    """Execute multiple sub-queries concurrently and merge results via RRF-style scoring."""

    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent

    async def retrieve(
        self,
        question: str,
        search_service: object,  # SearchService — avoids circular import
        top_k: int = 10,
        filters: SearchFilters | None = None,
        search_mode: SearchMode = SearchMode.HYBRID,
    ) -> dict:
        """Decompose *question*, search each sub-query, and return merged results.

        Returns:
            {
                "results": list[SearchResult],
                "sub_queries": list[str],
                "was_decomposed": bool,
                "total_retrieved": int,
            }
        """
        # 1. Decompose
        sub_queries = await query_decomposer.decompose(question)
        was_decomposed = len(sub_queries) > 1

        if was_decomposed:
            logger.info(
                "Query decomposed into %d sub-queries: %s",
                len(sub_queries),
                sub_queries,
            )

        # 2. Search each sub-query concurrently
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _search_one(query: str) -> list[SearchResult]:
            async with semaphore:
                req = SearchRequest(
                    query=query,
                    mode=search_mode,
                    limit=top_k,
                    filters=filters,
                )
                resp = await search_service.search(req)
                return resp.results

        all_results: list[list[SearchResult]] = await asyncio.gather(
            *[_search_one(q) for q in sub_queries]
        )

        # 3. Merge and deduplicate
        merged = self._merge_results(all_results)

        return {
            "results": merged[:top_k],
            "sub_queries": sub_queries,
            "was_decomposed": was_decomposed,
            "total_retrieved": len(merged),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _merge_results(
        self,
        all_results: list[list[SearchResult]],
    ) -> list[SearchResult]:
        """Merge per-query result lists: deduplicate by chunk_id, boost chunks
        that appeared in multiple sub-query results (higher hit_count = ranked
        earlier), break ties by accumulated score."""

        # chunk_id → {result, hit_count, total_score, best_rank}
        seen: dict[str, dict] = {}

        for query_idx, results in enumerate(all_results):
            for rank, result in enumerate(results):
                key = str(result.chunk_id)
                if key not in seen:
                    seen[key] = {
                        "result": result,
                        "hit_count": 1,
                        "total_score": result.score,
                        "best_rank": rank,
                    }
                else:
                    entry = seen[key]
                    entry["hit_count"] += 1
                    entry["total_score"] += result.score
                    entry["best_rank"] = min(entry["best_rank"], rank)

        sorted_entries = sorted(
            seen.values(),
            key=lambda e: (e["hit_count"], e["total_score"]),
            reverse=True,
        )
        return [e["result"] for e in sorted_entries]


# Module-level singleton — stateless, safe to share
multi_query_retriever = MultiQueryRetriever()
