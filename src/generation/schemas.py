"""Pydantic models for the generation pipeline."""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from src.retrieval.schemas import SearchFilters


class CitationStyle(str, Enum):
    """Citation formatting style."""

    INLINE = "inline"
    FOOTNOTE = "footnote"
    NONE = "none"


class QueryRequest(BaseModel):
    """RAG query request."""

    # Required
    question: str = Field(..., min_length=1, max_length=2000)

    # Optional context
    conversation_id: UUID | None = None

    # Search settings
    search_filters: SearchFilters | None = None
    max_sources: int = Field(default=5, ge=1, le=20)

    # Generation settings
    model: str | None = None
    temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    max_tokens: int = Field(default=1024, ge=100, le=4096)
    stream: bool = Field(default=True)

    # Relevance filtering — chunks below this threshold are excluded before generation.
    # Accepts a normalized [0, 1] value; mapped to RRF score space for hybrid search.
    min_relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)

    # Citation settings
    include_citations: bool = Field(default=True)
    citation_style: CitationStyle = Field(default=CitationStyle.INLINE)


class Source(BaseModel):
    """Citation source included in query response."""

    citation_id: int
    chunk_id: UUID
    document_id: UUID
    document_title: str
    filename: str
    page_number: int | None = None
    content_snippet: str
    relevance_score: float


class TokenUsage(BaseModel):
    """Token usage for billing."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class QueryResponse(BaseModel):
    """RAG query response (non-streaming)."""

    answer: str
    sources: list[Source]
    query_id: UUID
    conversation_id: UUID | None = None
    model: str
    usage: TokenUsage
    took_ms: float
    # Normalized [0, 1] confidence derived from mean source relevance.
    # 0.0 = no relevant chunks found; 1.0 = all sources maximally relevant.
    confidence_score: float = 0.0
    # True when the response was served from Redis cache (no LLM call made).
    cached: bool = False
    # Query decomposition details
    was_decomposed: bool = False
    sub_queries: list[str] = []
