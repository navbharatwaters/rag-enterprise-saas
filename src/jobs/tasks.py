"""Background job definitions for document processing.

Jobs are executed by ARQ workers and have access to the worker
context (db_factory, storage, embeddings, parser) set up in worker.py.
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.cache.query_cache import QueryCache
from src.documents.chunker import create_chunks
from src.documents.webhooks import send_document_webhook
from src.embeddings.batch import generate_embeddings_for_chunks
from src.models.document import Chunk, Document
from src.processing.chunk_validator import ChunkValidator
from src.processing.incremental import incremental_indexer
from src.processing.metadata_extractor import MetadataExtractor
from src.connectors.sync import run_connector_sync

CLEANUP_MAX_AGE_DAYS = 7

logger = logging.getLogger(__name__)


async def process_document(ctx: dict, document_id: str, tenant_id: str) -> dict:
    """Process an uploaded document end-to-end.

    Pipeline:
        1. Set tenant context (RLS)
        2. Load document record
        3. Download file from storage
        4. Parse with Docling (or text fallback)
        5. Chunk content
        6. Generate embeddings
        7. Store chunks with embeddings
        8. Update document status

    Args:
        ctx: Worker context with db_factory, storage, embeddings, parser.
        document_id: Document UUID as string.
        tenant_id: Tenant UUID as string.

    Returns:
        Dict with processing results.
    """
    doc_uuid = UUID(document_id)
    tenant_uuid = UUID(tenant_id)

    db_factory = ctx["db_factory"]
    storage = ctx["storage"]
    embeddings_client = ctx["embeddings"]
    parser = ctx["parser"]

    async with db_factory() as session:
        async with session.begin():
            # 1. Set tenant context for RLS
            await session.execute(
                text(f"SET LOCAL app.current_tenant_id = '{tenant_uuid}'")
            )

            # 2. Update status to processing
            await session.execute(
                update(Document)
                .where(Document.id == doc_uuid)
                .values(status="processing")
            )

        await send_document_webhook(
            "document.processing",
            document_id,
            tenant_id,
            {},
        )

        try:
            # 3. Load document record
            async with session.begin():
                await session.execute(
                    text(f"SET LOCAL app.current_tenant_id = '{tenant_uuid}'")
                )
                result = await session.execute(
                    select(Document).where(Document.id == doc_uuid)
                )
                document = result.scalar_one_or_none()

            if document is None:
                logger.error("Document %s not found", document_id)
                return {"status": "failed", "error": "Document not found"}

            # 4. Download file from storage
            logger.info(
                "Downloading %s from storage: %s",
                document.filename,
                document.storage_path,
            )
            file_bytes = await storage.download(document.storage_path)

            # 5. Parse document
            logger.info("Parsing %s (type: %s)", document.filename, document.file_type)
            parsed = await parser.parse(
                file_bytes, document.file_type, document.filename
            )

            if not parsed.content or not parsed.content.strip():
                raise ValueError("Parser returned empty content")

            # 5b. Extract metadata
            extractor = MetadataExtractor()
            doc_meta = extractor.extract(
                parsed.content, document.filename, document.file_type
            )

            # 6. Chunk content
            logger.info(
                "Chunking %d chars (%d words)",
                len(parsed.content),
                parsed.word_count,
            )
            chunk_data_list = create_chunks(
                document_id=doc_uuid,
                tenant_id=tenant_uuid,
                content=parsed.content,
                pages=parsed.pages or None,
                metadata={
                    "filename": document.filename,
                    "file_type": document.file_type,
                },
            )

            if not chunk_data_list:
                raise ValueError("Chunker produced no chunks")

            # 6b. Validate chunk quality
            validator = ChunkValidator()
            chunk_data_list, quality_scores = validator.filter_chunks(chunk_data_list)
            if not chunk_data_list:
                raise ValueError("All chunks were filtered out by quality validator")
            logger.info(
                "After quality filtering: %d chunks remain", len(chunk_data_list)
            )

            # 7. Incremental diff — only embed chunks that are new or changed
            async with session.begin():
                await session.execute(
                    text(f"SET LOCAL app.current_tenant_id = '{tenant_uuid}'")
                )
                # Find a previously completed document with same filename for this tenant
                prev_result = await session.execute(
                    select(Document.id)
                    .where(Document.tenant_id == tenant_uuid)
                    .where(Document.filename == document.filename)
                    .where(Document.status == "completed")
                    .where(Document.id != doc_uuid)
                    .order_by(Document.processed_at.desc())
                    .limit(1)
                )
                prev_doc_id = prev_result.scalar_one_or_none()

            if prev_doc_id is not None:
                logger.info(
                    "Incremental re-index: diffing against previous doc %s", prev_doc_id
                )
                async with session.begin():
                    await session.execute(
                        text(f"SET LOCAL app.current_tenant_id = '{tenant_uuid}'")
                    )
                    diff = await incremental_indexer.compute_diff(
                        session, str(prev_doc_id), chunk_data_list
                    )
                chunks_to_embed = diff["to_add"]
                unchanged_count = len(diff["unchanged"])
                logger.info(
                    "Incremental: +%d new, -%d removed, %d unchanged",
                    len(chunks_to_embed),
                    len(diff["to_delete"]),
                    unchanged_count,
                )
            else:
                diff = None
                chunks_to_embed = chunk_data_list
                unchanged_count = 0

            # Filter quality_scores to match chunks_to_embed
            if diff is not None:
                to_embed_set = {id(c) for c in chunks_to_embed}
                filtered_scores = [
                    q for c, q in zip(chunk_data_list, quality_scores)
                    if id(c) in to_embed_set
                ]
            else:
                filtered_scores = quality_scores

            # 7b. Generate embeddings (only for new/changed chunks)
            # Prepend user-provided metadata context to improve retrieval quality
            user_meta = document.user_metadata or {}
            context_prefix = ""
            if user_meta.get("description"):
                context_prefix = f"Document: {user_meta.get('title') or document.filename}\n"
                context_prefix += f"Description: {user_meta['description']}\n"
                if user_meta.get("category"):
                    context_prefix += f"Category: {user_meta['category']}\n"
                context_prefix += "\n"

            logger.info("Generating embeddings for %d chunks", len(chunks_to_embed))
            texts = [
                context_prefix + c.content if context_prefix else c.content
                for c in chunks_to_embed
            ]
            embeddings = await generate_embeddings_for_chunks(embeddings_client, texts) if texts else []

            # 8. Store chunks with embeddings
            logger.info("Storing %d chunks in database", len(chunks_to_embed))
            async with session.begin():
                await session.execute(
                    text(f"SET LOCAL app.current_tenant_id = '{tenant_uuid}'")
                )

                # Delete stale chunks from previous version
                if diff and diff["to_delete"]:
                    await incremental_indexer.delete_chunks(session, diff["to_delete"])

                for chunk_data, embedding, q_score in zip(chunks_to_embed, embeddings, filtered_scores):
                    chunk = Chunk(
                        tenant_id=tenant_uuid,
                        document_id=doc_uuid,
                        content=chunk_data.content,
                        content_hash=chunk_data.content_hash,
                        chunk_index=chunk_data.chunk_index,
                        page_number=chunk_data.page_number,
                        start_char=chunk_data.start_char,
                        end_char=chunk_data.end_char,
                        embedding=embedding,
                        metadata_=chunk_data.metadata,
                        token_count=chunk_data.token_count,
                        quality_score=q_score,
                    )
                    session.add(chunk)

                total_chunks = len(chunks_to_embed) + unchanged_count

                # 9. Update document status to completed
                extracted_meta = {
                    "title": doc_meta.title,
                    "author": doc_meta.author,
                    "language": doc_meta.language,
                    "summary": doc_meta.summary,
                    "has_tables": doc_meta.has_tables,
                    "has_code": doc_meta.has_code,
                    "detected_topics": doc_meta.detected_topics,
                }
                # Prefer user-supplied title; fall back to auto-extracted
                resolved_title = (
                    (document.user_metadata or {}).get("title")
                    or doc_meta.title
                    or document.filename
                )
                resolved_description = (document.user_metadata or {}).get("description")

                await session.execute(
                    update(Document)
                    .where(Document.id == doc_uuid)
                    .values(
                        status="completed",
                        processed_at=datetime.now(timezone.utc),
                        chunk_count=total_chunks,
                        page_count=parsed.page_count,
                        word_count=parsed.word_count,
                        extracted_metadata=extracted_meta,
                        title=resolved_title,
                        description=resolved_description,
                    )
                )

            logger.info(
                "Document %s processed: %d chunks (%d new, %d unchanged), %d pages",
                document_id,
                total_chunks,
                len(chunks_to_embed),
                unchanged_count,
                parsed.page_count,
            )
            await send_document_webhook(
                "document.completed",
                document_id,
                tenant_id,
                {
                    "chunk_count": total_chunks,
                    "page_count": parsed.page_count,
                    "word_count": parsed.word_count,
                    "incremental": diff is not None,
                },
            )

            # Invalidate query cache so the newly indexed content is reflected
            # immediately in subsequent queries.
            arq_redis = ctx.get("redis") or ctx.get("pool")
            if arq_redis is not None:
                await QueryCache(arq_redis).invalidate_tenant(tenant_id)

            return {
                "status": "completed",
                "chunks": total_chunks,
                "chunks_added": len(chunks_to_embed),
                "chunks_unchanged": unchanged_count,
                "pages": parsed.page_count,
                "words": parsed.word_count,
                "incremental": diff is not None,
            }

        except Exception as exc:
            logger.exception("Failed to process document %s: %s", document_id, exc)

            # Update document status to failed
            try:
                async with session.begin():
                    await session.execute(
                        text(f"SET LOCAL app.current_tenant_id = '{tenant_uuid}'")
                    )
                    await session.execute(
                        update(Document)
                        .where(Document.id == doc_uuid)
                        .values(
                            status="failed",
                            processing_error=str(exc)[:2000],
                        )
                    )
            except Exception as update_err:
                logger.error(
                    "Failed to update document %s status: %s",
                    document_id,
                    update_err,
                )

            await send_document_webhook(
                "document.failed",
                document_id,
                tenant_id,
                {"error": str(exc)[:500]},
            )
            return {"status": "failed", "error": str(exc)}


async def cleanup_failed_documents(ctx: dict) -> dict:
    """Remove failed documents older than CLEANUP_MAX_AGE_DAYS.

    This is a system-level cron job that operates across all tenants.
    Uses the admin database connection to bypass RLS.

    Steps:
        1. Query failed documents older than threshold
        2. Delete storage files for each document
        3. Delete database records (cascade deletes chunks)
    """
    from src.core.database import admin_engine

    storage = ctx["storage"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=CLEANUP_MAX_AGE_DAYS)

    AdminSession = async_sessionmaker(
        admin_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with AdminSession() as session:
        async with session.begin():
            # Find old failed documents
            result = await session.execute(
                select(Document)
                .where(Document.status == "failed")
                .where(Document.created_at < cutoff)
            )
            failed_docs = result.scalars().all()

            if not failed_docs:
                logger.info("No failed documents to clean up")
                return {"cleaned": 0}

            logger.info("Cleaning up %d failed documents", len(failed_docs))

            cleaned = 0
            for doc in failed_docs:
                # Delete storage files
                try:
                    await storage.delete(doc.tenant_id, doc.id)
                except Exception as exc:
                    logger.warning(
                        "Failed to delete storage for document %s: %s",
                        doc.id,
                        exc,
                    )

                # Delete database record (cascades to chunks)
                await session.delete(doc)
                cleaned += 1

            logger.info("Cleaned up %d failed documents", cleaned)
            return {"cleaned": cleaned}


async def sync_connector(ctx: dict, connector_id: str, tenant_id: str) -> dict:
    """Run a sync for a connector.

    Args:
        ctx: Worker context with db_factory.
        connector_id: Connector UUID as string.
        tenant_id: Tenant UUID as string.

    Returns:
        Dict with sync results.
    """
    connector_uuid = UUID(connector_id)
    tenant_uuid = UUID(tenant_id)

    db_factory = ctx["db_factory"]

    async with db_factory() as session:
        async with session.begin():
            await session.execute(
                text(f"SET LOCAL app.current_tenant_id = '{tenant_uuid}'")
            )

        try:
            result = await run_connector_sync(session, connector_uuid)
            logger.info(
                "Connector %s sync complete: found=%d processed=%d skipped=%d failed=%d",
                connector_id,
                result.files_found,
                result.files_processed,
                result.files_skipped,
                result.files_failed,
            )
            return {
                "status": "completed",
                "files_found": result.files_found,
                "files_processed": result.files_processed,
                "files_skipped": result.files_skipped,
                "files_failed": result.files_failed,
                "errors": result.errors,
            }
        except Exception as exc:
            logger.exception("Sync failed for connector %s: %s", connector_id, exc)
            return {"status": "failed", "error": str(exc)}


async def schedule_due_syncs(ctx: dict) -> dict:
    """Cron job: find connectors due for sync and enqueue sync jobs.

    Runs periodically (e.g., every 5 minutes) to check for connectors
    whose next_sync_at has passed and are in 'active' status.
    """
    from arq import ArqRedis

    db_factory = ctx["db_factory"]

    async with db_factory() as session:
        result = await session.execute(
            text("""
                SELECT id, tenant_id
                FROM connectors
                WHERE status = 'active'
                  AND next_sync_at IS NOT NULL
                  AND next_sync_at <= :now
                ORDER BY next_sync_at ASC
                LIMIT 50
            """),
            {"now": datetime.now(timezone.utc)},
        )
        due_connectors = result.fetchall()

    if not due_connectors:
        return {"queued": 0}

    # Enqueue sync jobs
    redis: ArqRedis = ctx.get("redis") or ctx.get("pool")
    queued = 0
    for row in due_connectors:
        try:
            await redis.enqueue_job(
                "sync_connector",
                str(row.id),
                str(row.tenant_id),
            )
            queued += 1
            logger.info("Queued sync for connector %s", row.id)
        except Exception as exc:
            logger.warning("Failed to enqueue sync for connector %s: %s", row.id, exc)

    logger.info("Scheduled %d connector syncs", queued)
    return {"queued": queued}
