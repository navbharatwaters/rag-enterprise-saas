"""FastAPI dependencies for the cache layer."""

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis

from src.cache.query_cache import QueryCache


async def get_redis(request: Request) -> Redis:
    """Return the app-scoped Redis client from application state."""
    return request.app.state.redis


async def get_query_cache(redis: Annotated[Redis, Depends(get_redis)]) -> QueryCache:
    return QueryCache(redis)


# Convenience type aliases for route signatures
RedisDep = Annotated[Redis, Depends(get_redis)]
QueryCacheDep = Annotated[QueryCache, Depends(get_query_cache)]
