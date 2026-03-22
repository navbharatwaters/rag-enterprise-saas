"""Redis-backed cache for RAG query responses.

Keys layout:
  query_cache:{tenant_id}:{sha256_prefix}   — cached response (TTL 1 hour)
  query_cache:stats:{tenant_id}:hits        — hit counter (no expiry)
  query_cache:stats:{tenant_id}:misses      — miss counter (no expiry)
"""

import hashlib
import json
from typing import Optional

from redis.asyncio import Redis

_STATS_PREFIX = "query_cache:stats"


class QueryCache:
    def __init__(self, redis: Redis):
        self.redis = redis
        self.ttl = 3600  # 1 hour

    def _make_key(self, tenant_id: str, question: str, filters: dict = None) -> str:
        """Create unique cache key from query params."""
        content = f"{tenant_id}:{question}:{json.dumps(filters or {}, sort_keys=True)}"
        hash_key = hashlib.sha256(content.encode()).hexdigest()[:16]
        return f"query_cache:{tenant_id}:{hash_key}"

    async def get(
        self, tenant_id: str, question: str, filters: dict = None
    ) -> Optional[dict]:
        """Get cached query result and increment hit/miss counters."""
        key = self._make_key(tenant_id, question, filters)
        data = await self.redis.get(key)
        if data:
            await self.redis.incr(f"{_STATS_PREFIX}:{tenant_id}:hits")
            return json.loads(data)
        await self.redis.incr(f"{_STATS_PREFIX}:{tenant_id}:misses")
        return None

    async def set(
        self,
        tenant_id: str,
        question: str,
        result: dict,
        filters: dict = None,
    ) -> None:
        """Cache a query result with TTL."""
        key = self._make_key(tenant_id, question, filters)
        await self.redis.setex(key, self.ttl, json.dumps(result))

    async def invalidate_tenant(self, tenant_id: str) -> int:
        """Delete all cached queries for a tenant.

        Called when documents are uploaded or deleted so that subsequent
        queries reflect the updated knowledge base.

        Returns the number of keys deleted.
        """
        # Collect data keys only (stats keys live under a different prefix)
        keys = [
            key
            async for key in self.redis.scan_iter(f"query_cache:{tenant_id}:*")
        ]
        if not keys:
            return 0
        return await self.redis.delete(*keys)

    async def get_stats(self, tenant_id: str) -> dict:
        """Return hit/miss counters and active key count for a tenant."""
        hits = int(await self.redis.get(f"{_STATS_PREFIX}:{tenant_id}:hits") or 0)
        misses = int(await self.redis.get(f"{_STATS_PREFIX}:{tenant_id}:misses") or 0)
        total = hits + misses

        cached_count = sum(
            1 async for _ in self.redis.scan_iter(f"query_cache:{tenant_id}:*")
        )

        return {
            "tenant_id": tenant_id,
            "cached_queries": cached_count,
            "hits": hits,
            "misses": misses,
            "total_requests": total,
            "hit_rate": round(hits / total, 3) if total > 0 else 0.0,
            "ttl_seconds": self.ttl,
        }
