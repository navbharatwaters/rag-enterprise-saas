"""Pydantic schemas for document API endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# --- Validation constants ---

ALLOWED_FILE_TYPES = {"pdf", "docx", "txt", "md", "html"}
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB


# --- Request/Response schemas ---


class DocumentUploadResponse(BaseModel):
    """Response for document upload (202 Accepted)."""

    id: UUID
    filename: str
    file_type: str
    file_size_bytes: int
    status: str = "pending"
    message: str = "Document accepted for processing"


class DocumentResponse(BaseModel):
    """Full document info."""

    id: UUID
    filename: str
    file_type: str
    file_size_bytes: int
    status: str
    title: str | None = None
    description: str | None = None
    processing_error: str | None = None
    processed_at: datetime | None = None
    chunk_count: int = 0
    page_count: int | None = None
    word_count: int | None = None
    custom_metadata: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    """Paginated list of documents."""

    items: list[DocumentResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class ChunkResponse(BaseModel):
    """Chunk info (without embedding)."""

    id: UUID
    chunk_index: int
    content: str
    content_hash: str
    page_number: int | None = None
    start_char: int | None = None
    end_char: int | None = None
    token_count: int
    metadata: dict = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class ChunkListResponse(BaseModel):
    """Paginated list of chunks."""

    items: list[ChunkResponse]
    total: int
    page: int
    page_size: int
    has_more: bool
