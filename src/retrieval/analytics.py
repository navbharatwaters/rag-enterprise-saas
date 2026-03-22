"""Search analytics logging.

Logs every search for analysis and relevance tuning.
Logging is fire-and-forget (async, doesn't block the response).
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from src.retrieval.schemas import SearchMode, SearchResponse

logger = logging.getLogger(__name__)


async def log_search(
    *,
    tenant_id: UUID,
    user_id: UUID,
    query: str,
    mode: SearchMode,
    filters: dict | None,
    result_count: int,
    took_ms: float,
    top_result_ids: list[UUID] | None = None,
) -> None:
    """Log a search event for analytics.

    This function is designed to be called without awaiting in the router
    (fire-and-forget) so it never blocks the search response.

    Currently logs to the Python logger. Can be extended to write to a
    search_logs table or external analytics service.

    Args:
        tenant_id: Tenant that performed the search.
        user_id: User that performed the search.
        query: Search query text.
        mode: Search mode used.
        filters: Applied filters (if any).
        result_count: Number of results returned.
        took_ms: Search duration in milliseconds.
        top_result_ids: IDs of top results (for relevance tuning).
    """
    logger.info(
        "search_event tenant=%s user=%s mode=%s results=%d took_ms=%.1f query=%r",
        tenant_id,
        user_id,
        mode.value,
        result_count,
        took_ms,
        _truncate(query, 100),
    )


def build_analytics_from_response(
    response: SearchResponse,
    tenant_id: UUID,
    user_id: UUID,
    filters: dict | None = None,
) -> dict:
    """Extract analytics fields from a SearchResponse.

    Returns a dict suitable for passing to log_search() as kwargs.
    """
    top_ids = [r.chunk_id for r in response.results[:10]]
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "query": response.query,
        "mode": response.mode,
        "filters": filters,
        "result_count": response.total,
        "took_ms": response.took_ms,
        "top_result_ids": top_ids,
    }


def _truncate(text: str, max_length: int) -> str:
    """Truncate text for logging (avoid logging full long queries)."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."
