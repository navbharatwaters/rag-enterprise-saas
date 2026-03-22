"""ARQ worker configuration and lifecycle.

Manages worker startup (initializing database, storage, embeddings,
and parser clients) and shutdown (cleaning up connections).
"""

import logging

from arq import cron
from arq.connections import RedisSettings

from src.core.config import settings
from src.core.database import AsyncSessionLocal, app_engine
from src.documents.parser import DoclingParser
from src.embeddings.client import EmbeddingsClient
from src.storage.minio import DocumentStorage
from src.jobs.tasks import (
    process_document,
    cleanup_failed_documents,
    sync_connector,
    schedule_due_syncs,
)

logger = logging.getLogger(__name__)


def _parse_redis_settings() -> RedisSettings:
    """Parse REDIS_URL into ARQ RedisSettings."""
    url = settings.REDIS_URL
    # redis://host:port/db
    url = url.replace("redis://", "")
    db = 0
    if "/" in url:
        url, db_str = url.rsplit("/", 1)
        db = int(db_str) if db_str else 0

    host = "localhost"
    port = 6379
    if ":" in url:
        host, port_str = url.split(":", 1)
        port = int(port_str)
    else:
        host = url

    return RedisSettings(host=host, port=port, database=db)


async def startup(ctx: dict) -> None:
    """Initialize worker context with shared clients.

    Called once when the worker process starts. All clients
    are stored in ctx and available to every job function.
    """
    logger.info("Worker starting up...")

    ctx["db_factory"] = AsyncSessionLocal
    ctx["storage"] = DocumentStorage()
    ctx["embeddings"] = EmbeddingsClient()
    ctx["parser"] = DoclingParser()

    # Ensure MinIO bucket exists
    try:
        await ctx["storage"].ensure_bucket()
    except Exception:
        logger.warning("Could not ensure MinIO bucket exists (MinIO may be unavailable)")

    logger.info("Worker startup complete")


async def shutdown(ctx: dict) -> None:
    """Clean up worker resources.

    Called once when the worker process is shutting down.
    """
    logger.info("Worker shutting down...")

    # Dispose the SQLAlchemy engine to close all connections
    await app_engine.dispose()

    logger.info("Worker shutdown complete")


class WorkerSettings:
    """ARQ worker settings.

    ARQ discovers this class and uses it to configure the worker.
    Job functions are imported here to register them with the worker.
    """

    functions = [
        process_document,
        cleanup_failed_documents,
        sync_connector,
        schedule_due_syncs,
    ]

    cron_jobs = [
        cron(cleanup_failed_documents, hour=3, minute=0),
        cron(schedule_due_syncs, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
    ]

    on_startup = startup
    on_shutdown = shutdown

    redis_settings = _parse_redis_settings()

    # Worker configuration
    max_jobs = 10
    job_timeout = 600  # 10 minutes max per job
    keep_result = 3600  # Keep results for 1 hour
    health_check_interval = 30  # Seconds between health checks
