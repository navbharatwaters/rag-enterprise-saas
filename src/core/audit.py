"""Audit logging helper for SOC 2 compliance.

All user-initiated actions should be logged via audit_log().
The audit_logs table is append-only (rag_app cannot UPDATE/DELETE).
"""

import logging
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.audit import AuditLog

logger = logging.getLogger(__name__)


def _get_client_ip(request: Request | None) -> str | None:
    """Extract client IP from request, respecting X-Forwarded-For."""
    if request is None:
        return None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _get_user_agent(request: Request | None) -> str | None:
    """Extract User-Agent from request."""
    if request is None:
        return None
    return request.headers.get("User-Agent")


async def audit_log(
    db: AsyncSession,
    tenant_id: UUID,
    action: str,
    resource_type: str,
    user_id: UUID | None = None,
    api_key_id: UUID | None = None,
    resource_id: UUID | None = None,
    details: dict | None = None,
    request: Request | None = None,
) -> None:
    """Write an audit log entry. Required for SOC 2 compliance.

    Args:
        db: Database session (should have RLS context set).
        tenant_id: Tenant ID for the action.
        action: Action identifier (e.g. "api_key.create", "document.upload").
        resource_type: Type of resource (e.g. "api_key", "document").
        user_id: Internal user ID (for JWT-authenticated requests).
        api_key_id: API key ID (for API-key-authenticated requests).
        resource_id: ID of the affected resource.
        details: Additional context as JSON.
        request: FastAPI request for IP/user-agent extraction.
    """
    log = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        api_key_id=api_key_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        ip_address=_get_client_ip(request),
        user_agent=_get_user_agent(request),
    )
    db.add(log)
    # Don't commit here - let the caller's transaction handle it.
    # The middleware wraps requests in a transaction via session.begin().
    await db.flush()
    logger.info(
        "audit action=%s resource=%s/%s tenant=%s user=%s",
        action,
        resource_type,
        resource_id,
        tenant_id,
        user_id or api_key_id,
    )
