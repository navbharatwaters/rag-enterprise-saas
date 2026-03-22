"""Pydantic schemas for search API request/response."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SearchMode(str, Enum):
    """Search strategy to use."""

    HYBRID = "hybrid"
    BM25_ONLY = "bm25"
    VECTOR_ONLY = "vector"


class SearchFilters(BaseModel):
    """Optional filters to narrow search results."""

    # Restrict to specific documents
    document_ids: list[UUID] | None = None

    # Restrict to file types (e.g. ["pdf", "docx"])
    file_types: list[str] | None = None

    # Date range on document creation
    created_after: datetime | None = None
    created_before: datetime | None = None

    # JSONB containment filter on chunk metadata
    metadata: dict[str, Any] | None = None

    # User-metadata filters
    category: str | None = None
    tags: list[str] | None = None
    confidentiality: str | None = None
    document_date_from: str | None = None
    document_date_to: str | None = None


class SearchRequest(BaseModel):
    """Search request parameters."""

    # Required
    query: str = Field(..., min_length=1, max_length=1000)

    # Pagination
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    # Search mode
    mode: SearchMode = Field(default=SearchMode.HYBRID)

    # Filters
    filters: SearchFilters | None = None

    # Relevance filtering — filters out results below this raw score threshold.
    # Score scale is mode-dependent: hybrid RRF scores are ~0.01–0.033,
    # vector scores are 0–1, BM25 scores are unbounded.
    min_relevance_score: float | None = Field(default=None, ge=0.0)

    # Options
    include_content: bool = Field(default=True)
    highlight: bool = Field(default=True)
    highlight_max_length: int = Field(default=200, ge=50, le=1000)


class SearchResult(BaseModel):
    """Individual search result."""

    # Chunk info
    chunk_id: UUID
    content: str | None = None

    # Document info
    document_id: UUID
    document_title: str
    filename: str
    file_type: str

    # Position
    chunk_index: int
    page_number: int | None = None

    # Scoring
    score: float
    bm25_rank: int | None = None
    vector_rank: int | None = None

    # Highlighting
    highlights: list[str] | None = None

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """Search response with results and metadata."""

    results: list[SearchResult]
    total: int
    query: str
    mode: SearchMode
    took_ms: float
