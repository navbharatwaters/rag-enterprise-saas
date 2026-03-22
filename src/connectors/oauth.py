"""OAuth flow management — state generation, code exchange, token refresh."""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.connectors.encryption import encrypt_credentials
from src.connectors.registry import get_connector_instance
from src.connectors.schemas import ConnectorStatus
from src.core.config import settings

logger = logging.getLogger(__name__)

# In-memory state store (Redis-backed in production; simple dict for now)
_oauth_states: dict[str, dict] = {}

# State token expiry
STATE_TTL = timedelta(minutes=10)


class OAuthError(Exception):
    """Raised when an OAuth operation fails."""


def generate_state(tenant_id: UUID, connector_type: str, name: str, config: dict) -> str:
    """Generate a CSRF-safe state token and store metadata for callback.

    Returns the state token string.
    """
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "tenant_id": str(tenant_id),
        "connector_type": connector_type,
        "name": name,
        "config": config,
        "created_at": datetime.now(timezone.utc),
    }
    return state


def validate_state(state: str) -> dict | None:
    """Validate and consume a state token. Returns metadata or None if invalid/expired."""
    meta = _oauth_states.pop(state, None)
    if meta is None:
        return None

    created = meta["created_at"]
    if datetime.now(timezone.utc) - created > STATE_TTL:
        return None

    return meta


def cleanup_expired_states() -> int:
    """Remove expired state tokens. Returns number removed."""
    now = datetime.now(timezone.utc)
    expired = [
        s for s, m in _oauth_states.items()
        if now - m["created_at"] > STATE_TTL
    ]
    for s in expired:
        _oauth_states.pop(s, None)
    return len(expired)


async def start_oauth(
    tenant_id: UUID,
    connector_type: str,
    name: str,
    config: dict,
) -> dict:
    """Start an OAuth flow: generate state, build auth URL.

    Returns {"auth_url": str, "state": str}.
    """
    state = generate_state(tenant_id, connector_type, name, config)

    # Create a temporary connector instance (no credentials yet) to get the URL
    connector = get_connector_instance(connector_type, config, {})
    if not connector.supports_oauth:
        raise OAuthError(f"Connector type '{connector_type}' does not support OAuth")

    redirect_uri = settings.GOOGLE_REDIRECT_URI
    auth_url = await connector.get_oauth_url(redirect_uri, state)

    return {"authorization_url": auth_url, "state": state}


async def complete_oauth(
    db: AsyncSession,
    state: str,
    code: str,
) -> dict:
    """Complete an OAuth flow: validate state, exchange code, create connector.

    Returns the created connector dict.
    """
    meta = validate_state(state)
    if meta is None:
        raise OAuthError("Invalid or expired OAuth state token")

    connector_type = meta["connector_type"]
    tenant_id = UUID(meta["tenant_id"])
    name = meta["name"]
    config = meta["config"]

    # Exchange the authorization code for tokens
    connector = get_connector_instance(connector_type, config, {})
    redirect_uri = settings.GOOGLE_REDIRECT_URI
    credentials = await connector.exchange_code(code, redirect_uri)

    # Encrypt and persist
    encrypted = encrypt_credentials(credentials)

    import json
    result = await db.execute(
        text("""
            INSERT INTO connectors (
                tenant_id, connector_type, name, config,
                credentials_encrypted, sync_frequency, status
            ) VALUES (
                :tenant_id, :connector_type, :name, :config::jsonb,
                :credentials_encrypted, :sync_frequency, :status
            )
            RETURNING id, created_at, updated_at
        """),
        {
            "tenant_id": tenant_id,
            "connector_type": connector_type,
            "name": name,
            "config": json.dumps(config),
            "credentials_encrypted": encrypted,
            "sync_frequency": "daily",
            "status": ConnectorStatus.ACTIVE,
        },
    )
    row = result.fetchone()
    await db.flush()

    return {
        "id": row.id,
        "connector_type": connector_type,
        "name": name,
        "config": config,
        "status": ConnectorStatus.ACTIVE,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def refresh_google_token(
    db: AsyncSession,
    connector_id: UUID,
    credentials: dict,
) -> dict:
    """Refresh an expired Google OAuth access token.

    Args:
        db: Database session.
        connector_id: Connector whose token to refresh.
        credentials: Decrypted credentials dict containing refresh_token.

    Returns:
        Updated credentials dict with new access_token.

    Raises:
        OAuthError: If refresh fails or no refresh token available.
    """
    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        raise OAuthError("No refresh token available — user must re-authorize")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=15.0,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Token refresh failed: %s", exc.response.text)
        raise OAuthError("Token refresh failed — user may need to re-authorize") from exc
    except httpx.HTTPError as exc:
        raise OAuthError(f"Token refresh request failed: {exc}") from exc

    # Update credentials
    updated = {**credentials}
    updated["access_token"] = data["access_token"]
    if "refresh_token" in data:
        updated["refresh_token"] = data["refresh_token"]

    # Persist updated credentials
    encrypted = encrypt_credentials(updated)
    await db.execute(
        text("UPDATE connectors SET credentials_encrypted = :creds WHERE id = :id"),
        {"creds": encrypted, "id": connector_id},
    )
    await db.flush()

    return updated
