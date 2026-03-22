"""Cache management API endpoints."""

from fastapi import APIRouter

from src.auth.dependencies import CurrentTenant, CurrentUser
from src.cache.dependencies import QueryCacheDep

router = APIRouter(prefix="/api/v1/cache", tags=["cache"])


@router.get("/stats")
async def cache_stats(
    user: CurrentUser,
    tenant_id: CurrentTenant,
    cache: QueryCacheDep,
):
    """Return cache hit/miss statistics and active query count for the tenant.

    Useful for monitoring cache effectiveness and tuning TTL.
    """
    return await cache.get_stats(str(tenant_id))
