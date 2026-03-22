"""API key generation and verification.

API keys have format: rag_sk_<32 hex chars>
Keys are SHA-256 hashed before storage. Only the prefix (first 8 chars)
is stored in plaintext for identification in the UI.
"""

import hashlib
import secrets
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.exceptions import ApiKeyError
from src.models.api_key import ApiKey

API_KEY_PREFIX = "rag_sk_"


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        Tuple of (full_key, key_hash, key_prefix).
        The full_key is shown to the user once and never stored.
    """
    raw = secrets.token_hex(32)
    full_key = f"{API_KEY_PREFIX}{raw}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    key_prefix = full_key[: len(API_KEY_PREFIX) + 8]  # rag_sk_ + 8 hex chars
    return full_key, key_hash, key_prefix


def hash_api_key(key: str) -> str:
    """Hash an API key for lookup."""
    return hashlib.sha256(key.encode()).hexdigest()


async def verify_api_key(db: AsyncSession, key: str) -> ApiKey:
    """Verify an API key and return the ApiKey record.

    Args:
        db: Admin database session (not RLS-scoped, since we don't know tenant yet).
        key: The full API key string.

    Returns:
        ApiKey model instance.

    Raises:
        ApiKeyError: If key is invalid, revoked, or expired.
    """
    if not key.startswith(API_KEY_PREFIX):
        raise ApiKeyError("Invalid API key format")

    key_hash = hash_api_key(key)

    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash)
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise ApiKeyError("Invalid API key")

    if api_key.revoked_at is not None:
        raise ApiKeyError("API key has been revoked")

    if api_key.expires_at is not None and api_key.expires_at < datetime.now(timezone.utc):
        raise ApiKeyError("API key has expired")

    return api_key


async def update_api_key_usage(db: AsyncSession, api_key_id: UUID) -> None:
    """Update last_used_at and increment total_requests for an API key."""
    await db.execute(
        update(ApiKey)
        .where(ApiKey.id == api_key_id)
        .values(
            last_used_at=datetime.now(timezone.utc),
            total_requests=ApiKey.total_requests + 1,
        )
    )
