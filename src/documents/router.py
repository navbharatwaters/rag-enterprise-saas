"""Document API endpoints for upload, status, and management."""

import logging
from uuid import UUID

from arq.connections import ArqRedis, create_pool
from fastapi import APIRouter, Form, HTTPException, UploadFile, File, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import CurrentUser, CurrentTenant, TenantDB
from src.billing.metering import record_usage
from src.cache.dependencies import QueryCacheDep
from src.documents.dedup import check_duplicate, compute_file_hash
from src.billing.quotas import (
    QuotaExceededError,
    enforce_document_quota,
    enforce_storage_quota,
)
from src.billing.service import get_tenant_billing_info
from src.core.audit import audit_log
from src.core.config import settings
from src.documents.schemas import (
    ALLOWED_FILE_TYPES,
    MAX_FILE_SIZE_BYTES,
    ChunkListResponse,
    ChunkResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from src.models.document import Chunk, Document
from src.storage.minio import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

# ARQ Redis pool (lazy-initialized)
_arq_pool: ArqRedis | None = None


async def _get_arq_pool() -> ArqRedis:
    """Get or create ARQ Redis connection pool."""
    global _arq_pool
    if _arq_pool is None:
        from src.jobs.worker import _parse_redis_settings

        _arq_pool = await create_pool(_parse_redis_settings())
    return _arq_pool


def _get_file_type(filename: str) -> str:
    """Extract file extension without dot."""
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


@router.post("", status_code=202, response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    description: str | None = Form(None),
    category: str | None = Form(None),
    tags: str | None = Form(None),
    author: str | None = Form(None),
    document_date: str | None = Form(None),
    confidentiality: str = Form("internal"),
    *,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
    cache: QueryCacheDep,
):
    """Upload a document for processing.

    Validates the file, stores it in MinIO, creates a database record,
    and enqueues a background processing job.

    Returns 202 Accepted with the document ID.
    """
    # Validate filename
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    file_type = _get_file_type(file.filename)
    if file_type not in ALLOWED_FILE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: '{file_type}'. Allowed: {', '.join(sorted(ALLOWED_FILE_TYPES))}",
        )

    # Read file content
    content = await file.read()

    # Validate file size
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB",
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="File is empty")

    # Deduplication check
    file_hash = compute_file_hash(content)
    existing = await check_duplicate(db, tenant_id, file_hash)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "duplicate_document",
                "message": "This file has already been uploaded.",
                "existing_document_id": str(existing.id),
                "existing_filename": existing.filename,
                "existing_status": existing.status,
            },
        )

    # Enforce billing quotas
    try:
        tier, _stripe_cid = await get_tenant_billing_info(db, tenant_id)
        await enforce_document_quota(db, tenant_id, tier)
        await enforce_storage_quota(db, tenant_id, tier, additional_bytes=len(content))
    except QuotaExceededError as e:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "message": f"{e.quota_type.title()} limit reached. Upgrade your plan for more capacity.",
                "quota_type": e.quota_type,
                "limit": e.limit,
                "current": e.current,
            },
        )

    # Build user-provided metadata
    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    user_metadata = {
        k: v for k, v in {
            "title": title,
            "description": description,
            "category": category,
            "tags": tag_list,
            "author": author,
            "document_date": document_date,
            "confidentiality": confidentiality,
        }.items() if v is not None and v != [] and v != ""
    }

    # Store in MinIO
    storage = get_storage()
    document = Document(
        tenant_id=tenant_id,
        filename=file.filename,
        file_type=file_type,
        file_size_bytes=len(content),
        storage_path="",  # Will be set after upload
        status="pending",
        uploaded_by=user.user_id,
        file_hash=file_hash,
        user_metadata=user_metadata,
    )
    db.add(document)
    await db.flush()  # Get the generated ID

    storage_path = await storage.upload(
        tenant_id=tenant_id,
        document_id=document.id,
        data=content,
        filename=file.filename,
        content_type=file.content_type or "application/octet-stream",
    )
    document.storage_path = storage_path

    # Record usage and audit log BEFORE commit — they share the same transaction.
    # audit_log() only flushes; the middleware's session.begin() context commits on exit.
    try:
        await record_usage(
            db=db,
            tenant_id=tenant_id,
            stripe_customer_id=_stripe_cid,
            event_name="document_uploaded",
        )
    except Exception:
        logger.warning("Failed to record upload usage for tenant %s", tenant_id, exc_info=True)

    await audit_log(
        db=db,
        tenant_id=tenant_id,
        user_id=user.user_id,
        action="document.upload",
        resource_type="document",
        resource_id=document.id,
        details={
            "filename": file.filename,
            "file_type": file_type,
            "file_size_bytes": len(content),
        },
    )

    await db.flush()

    # Enqueue processing job (after commit so document row is visible to worker)
    try:
        pool = await _get_arq_pool()
        await pool.enqueue_job(
            "process_document",
            str(document.id),
            str(tenant_id),
        )
        logger.info("Enqueued processing job for document %s", document.id)
    except Exception as exc:
        logger.error("Failed to enqueue job for document %s: %s", document.id, exc)
        raise HTTPException(status_code=503, detail="Processing service unavailable")

    # Invalidate cached queries so they reflect the incoming document once processed.
    await cache.invalidate_tenant(str(tenant_id))

    return DocumentUploadResponse(
        id=document.id,
        filename=file.filename,
        file_type=file_type,
        file_size_bytes=len(content),
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    *,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
):
    """Get document details by ID."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    return DocumentResponse.model_validate(document)


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    *,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
):
    """List documents with pagination and optional status filter."""
    query = select(Document).order_by(Document.created_at.desc())
    count_query = select(func.count()).select_from(Document)

    if status:
        query = query.where(Document.status == status)
        count_query = count_query.where(Document.status == status)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get paginated results
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    result = await db.execute(query)
    documents = result.scalars().all()

    return DocumentListResponse(
        items=[DocumentResponse.model_validate(d) for d in documents],
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + page_size) < total,
    )


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: UUID,
    *,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
    cache: QueryCacheDep,
):
    """Delete a document, its chunks, and storage files."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete storage files
    try:
        storage = get_storage()
        await storage.delete(tenant_id, document_id)
    except Exception as exc:
        logger.warning("Failed to delete storage for document %s: %s", document_id, exc)

    # Delete document (cascade deletes chunks)
    await db.delete(document)
    await db.flush()

    # Audit log
    await audit_log(
        db=db,
        tenant_id=tenant_id,
        user_id=user.user_id,
        action="document.delete",
        resource_type="document",
        resource_id=document_id,
        details={"filename": document.filename},
    )

    await cache.invalidate_tenant(str(tenant_id))


@router.get("/{document_id}/chunks", response_model=ChunkListResponse)
async def list_chunks(
    document_id: UUID,
    *,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List chunks for a document with pagination.

    Returns chunks ordered by chunk_index. Embeddings are excluded
    from the response to keep payloads small.
    """
    # Verify document exists
    doc_result = await db.execute(
        select(Document.id).where(Document.id == document_id)
    )
    if doc_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Count total chunks
    count_result = await db.execute(
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.document_id == document_id)
    )
    total = count_result.scalar() or 0

    # Get paginated chunks
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index)
        .offset(offset)
        .limit(page_size)
    )
    chunks = result.scalars().all()

    return ChunkListResponse(
        items=[
            ChunkResponse(
                id=c.id,
                chunk_index=c.chunk_index,
                content=c.content,
                content_hash=c.content_hash,
                page_number=c.page_number,
                start_char=c.start_char,
                end_char=c.end_char,
                token_count=c.token_count,
                metadata=c.metadata_,
                created_at=c.created_at,
            )
            for c in chunks
        ],
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + page_size) < total,
    )
