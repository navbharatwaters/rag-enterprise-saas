"""Quota checking for billing enforcement."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.billing.constants import TIER_LIMITS, SubscriptionTier
from src.billing.metering import get_monthly_usage


class QuotaExceededError(Exception):
    """Raised when a tenant exceeds their plan quota."""

    def __init__(self, quota_type: str, limit: int, current: int):
        self.quota_type = quota_type
        self.limit = limit
        self.current = current
        super().__init__(f"{quota_type} quota exceeded: {current}/{limit}")


async def check_query_quota(
    db: AsyncSession,
    tenant_id: UUID,
    tier: SubscriptionTier,
) -> bool:
    """Check if tenant can make more queries this month."""
    limits = TIER_LIMITS[tier]
    if limits["queries_per_month"] == -1:
        return True
    current = await get_monthly_usage(db, tenant_id, "query_executed")
    return current < limits["queries_per_month"]


async def check_document_quota(
    db: AsyncSession,
    tenant_id: UUID,
    tier: SubscriptionTier,
) -> bool:
    """Check if tenant can upload more documents."""
    limits = TIER_LIMITS[tier]
    if limits["documents"] == -1:
        return True

    result = await db.execute(
        text("""
            SELECT COUNT(*) FROM documents
            WHERE tenant_id = :tenant_id AND deleted_at IS NULL
        """),
        {"tenant_id": tenant_id},
    )
    current = result.scalar() or 0
    return current < limits["documents"]


async def check_storage_quota(
    db: AsyncSession,
    tenant_id: UUID,
    tier: SubscriptionTier,
    additional_bytes: int = 0,
) -> bool:
    """Check if tenant has storage capacity for additional_bytes."""
    limits = TIER_LIMITS[tier]
    if limits["storage_gb"] == -1:
        return True

    result = await db.execute(
        text("""
            SELECT COALESCE(SUM(file_size_bytes), 0) FROM documents
            WHERE tenant_id = :tenant_id AND deleted_at IS NULL
        """),
        {"tenant_id": tenant_id},
    )
    current_bytes = result.scalar() or 0
    limit_bytes = int(limits["storage_gb"] * 1024 * 1024 * 1024)
    return (current_bytes + additional_bytes) < limit_bytes


async def enforce_query_quota(
    db: AsyncSession,
    tenant_id: UUID,
    tier: SubscriptionTier,
) -> None:
    """Raise QuotaExceededError if query quota is exceeded."""
    limits = TIER_LIMITS[tier]
    if limits["queries_per_month"] == -1:
        return

    current = await get_monthly_usage(db, tenant_id, "query_executed")
    if current >= limits["queries_per_month"]:
        raise QuotaExceededError("queries", limits["queries_per_month"], current)


async def enforce_document_quota(
    db: AsyncSession,
    tenant_id: UUID,
    tier: SubscriptionTier,
) -> None:
    """Raise QuotaExceededError if document quota is exceeded."""
    limits = TIER_LIMITS[tier]
    if limits["documents"] == -1:
        return

    result = await db.execute(
        text("""
            SELECT COUNT(*) FROM documents
            WHERE tenant_id = :tenant_id AND deleted_at IS NULL
        """),
        {"tenant_id": tenant_id},
    )
    current = result.scalar() or 0
    if current >= limits["documents"]:
        raise QuotaExceededError("documents", limits["documents"], current)


async def enforce_connector_quota(
    db: AsyncSession,
    tenant_id: UUID,
    tier: SubscriptionTier,
) -> None:
    """Raise QuotaExceededError if connector quota is exceeded."""
    limits = TIER_LIMITS[tier]
    if limits["connectors"] == -1:
        return

    result = await db.execute(
        text("""
            SELECT COUNT(*) FROM connectors
            WHERE tenant_id = :tenant_id
        """),
        {"tenant_id": tenant_id},
    )
    current = result.scalar() or 0
    if current >= limits["connectors"]:
        raise QuotaExceededError("connectors", limits["connectors"], current)


async def enforce_storage_quota(
    db: AsyncSession,
    tenant_id: UUID,
    tier: SubscriptionTier,
    additional_bytes: int = 0,
) -> None:
    """Raise QuotaExceededError if storage quota is exceeded."""
    limits = TIER_LIMITS[tier]
    if limits["storage_gb"] == -1:
        return

    result = await db.execute(
        text("""
            SELECT COALESCE(SUM(file_size_bytes), 0) FROM documents
            WHERE tenant_id = :tenant_id AND deleted_at IS NULL
        """),
        {"tenant_id": tenant_id},
    )
    current_bytes = result.scalar() or 0
    limit_bytes = int(limits["storage_gb"] * 1024 * 1024 * 1024)
    if (current_bytes + additional_bytes) >= limit_bytes:
        raise QuotaExceededError(
            "storage",
            limit_bytes,
            current_bytes + additional_bytes,
        )
