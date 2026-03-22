"""Batch document upload endpoints."""

import json
import logging
from uuid import UUID

from arq.connections import ArqRedis, create_pool
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from src.auth.dependencies import CurrentTenant, CurrentUser, TenantDB
from src.billing.metering import record_usage
from src.billing.quotas import QuotaExceededError, enforce_document_quota, enforce_storage_quota
from src.billing.service import get_tenant_billing_info
from src.cache.dependencies import QueryCacheDep
from src.core.audit import audit_log
from src.documents.batch import BatchProcessor, batch_processor
from src.documents.dedup import check_duplicate, compute_file_hash
from src.documents.schemas import ALLOWED_FILE_TYPES, MAX_FILE_SIZE_BYTES
from src.models.document import Document
from src.storage.minio import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents/batch", tags=["batch"])

_arq_pool: ArqRedis | None = None


async def _get_arq_pool() -> ArqRedis:
    global _arq_pool
    if _arq_pool is None:
        from src.jobs.worker import _parse_redis_settings
        _arq_pool = await create_pool(_parse_redis_settings())
    return _arq_pool


def _get_file_type(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


# --- Schemas ---

class BatchUploadResponse(BaseModel):
    batch_id: UUID
    total_documents: int
    accepted: int
    rejected: int
    status: str
    document_ids: list[UUID]
    rejections: list[dict]
    message: str


class BatchStatusResponse(BaseModel):
    id: str
    status: str
    total: int
    processed: int
    succeeded: int
    failed: int
    pending: int
    progress_pct: float
    created_at: str | None = None
    completed_at: str | None = None


# --- Endpoints ---

@router.post("", response_model=BatchUploadResponse, status_code=202)
async def batch_upload(
    files: list[UploadFile] = File(...),
    metadata_json: str | None = Form(None),
    *,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
    cache: QueryCacheDep,
):
    """Upload up to 20 documents in a single request.

    Each file is independently validated. Valid files are stored and queued
    for processing. Invalid files are reported in the response without
    aborting the batch. Returns 202 Accepted with a batch_id for status polling.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    # Parse per-file metadata array (indexed by position)
    per_file_metadata: list[dict] = []
    if metadata_json:
        try:
            per_file_metadata = json.loads(metadata_json)
            if not isinstance(per_file_metadata, list):
                per_file_metadata = []
        except (json.JSONDecodeError, ValueError):
            per_file_metadata = []

    if len(files) > BatchProcessor.MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {BatchProcessor.MAX_BATCH_SIZE} files per batch",
        )

    # Enforce per-tenant billing quotas for the whole batch up-front
    try:
        tier, stripe_cid = await get_tenant_billing_info(db, tenant_id)
        await enforce_document_quota(db, tenant_id, tier)
    except QuotaExceededError as e:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "message": f"Document limit reached. Upgrade your plan.",
                "quota_type": e.quota_type,
                "limit": e.limit,
                "current": e.current,
            },
        )

    storage = get_storage()
    document_ids: list[UUID] = []
    rejections: list[dict] = []

    for file_idx, file in enumerate(files):
        fname = file.filename or ""

        # --- per-file validation ---
        file_type = _get_file_type(fname)
        if not fname:
            rejections.append({"filename": "(empty)", "reason": "Filename is required"})
            continue
        if file_type not in ALLOWED_FILE_TYPES:
            rejections.append({"filename": fname, "reason": f"Unsupported file type: '{file_type}'"})
            continue

        content = await file.read()
        if len(content) == 0:
            rejections.append({"filename": fname, "reason": "File is empty"})
            continue
        if len(content) > MAX_FILE_SIZE_BYTES:
            rejections.append({
                "filename": fname,
                "reason": f"File too large (max {MAX_FILE_SIZE_BYTES // (1024*1024)} MB)",
            })
            continue

        # Dedup check
        file_hash = compute_file_hash(content)
        existing = await check_duplicate(db, tenant_id, file_hash)
        if existing is not None:
            rejections.append({
                "filename": fname,
                "reason": "Duplicate file already uploaded",
                "existing_document_id": str(existing.id),
            })
            continue

        # Storage quota per file
        try:
            await enforce_storage_quota(db, tenant_id, tier, additional_bytes=len(content))
        except QuotaExceededError as e:
            rejections.append({"filename": fname, "reason": "Storage quota exceeded"})
            continue

        # Build per-file user metadata
        raw_meta = per_file_metadata[file_idx] if file_idx < len(per_file_metadata) else {}
        tags_raw = raw_meta.get("tags", [])
        tag_list = [t.strip() for t in tags_raw.split(",")] if isinstance(tags_raw, str) else (tags_raw if isinstance(tags_raw, list) else [])
        file_user_metadata = {
            k: v for k, v in {
                "title": raw_meta.get("title"),
                "description": raw_meta.get("description"),
                "category": raw_meta.get("category"),
                "tags": tag_list,
                "author": raw_meta.get("author"),
                "document_date": raw_meta.get("document_date"),
                "confidentiality": raw_meta.get("confidentiality", "internal"),
            }.items() if v is not None and v != [] and v != ""
        }

        # Create document record
        document = Document(
            tenant_id=tenant_id,
            filename=fname,
            file_type=file_type,
            file_size_bytes=len(content),
            storage_path="",
            status="pending",
            uploaded_by=user.user_id,
            file_hash=file_hash,
            user_metadata=file_user_metadata,
        )
        db.add(document)
        await db.flush()

        storage_path = await storage.upload(
            tenant_id=tenant_id,
            document_id=document.id,
            data=content,
            filename=fname,
            content_type=file.content_type or "application/octet-stream",
        )
        document.storage_path = storage_path

        try:
            await record_usage(
                db=db,
                tenant_id=tenant_id,
                stripe_customer_id=stripe_cid,
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
            details={"filename": fname, "file_type": file_type, "file_size_bytes": len(content), "batch": True},
        )
        await db.flush()
        document_ids.append(document.id)

    if not document_ids:
        raise HTTPException(
            status_code=422,
            detail={"message": "All files were rejected", "rejections": rejections},
        )

    # Create batch job record
    batch = await batch_processor.create_batch(db, str(tenant_id), document_ids)
    await db.flush()

    # Enqueue individual processing jobs
    pool = await _get_arq_pool()
    for doc_id in document_ids:
        try:
            await pool.enqueue_job("process_document", str(doc_id), str(tenant_id))
        except Exception as exc:
            logger.error("Failed to enqueue job for document %s: %s", doc_id, exc)

    await cache.invalidate_tenant(str(tenant_id))

    return BatchUploadResponse(
        batch_id=batch.id,
        total_documents=len(files),
        accepted=len(document_ids),
        rejected=len(rejections),
        status=batch.status.value,
        document_ids=document_ids,
        rejections=rejections,
        message=f"{len(document_ids)} document(s) accepted for processing, {len(rejections)} rejected.",
    )


@router.get("/{batch_id}", response_model=BatchStatusResponse)
async def get_batch_status(
    batch_id: UUID,
    *,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
):
    """Poll the status of a batch upload job."""
    status = await batch_processor.get_batch_status(db, batch_id, str(tenant_id))
    if status is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return BatchStatusResponse(**status)
