"""Connector API endpoints — CRUD, sync trigger, OAuth flow."""

import logging
from uuid import UUID

from arq.connections import ArqRedis, create_pool
from fastapi import APIRouter, HTTPException

from src.auth.dependencies import CurrentTenant, CurrentUser, TenantDB
from src.billing.quotas import QuotaExceededError, enforce_connector_quota
from src.billing.service import get_tenant_billing_info
from src.connectors.oauth import OAuthError, complete_oauth, start_oauth
from src.connectors.schemas import (
    ConnectorResponse,
    CreateConnectorRequest,
    OAuthStartResponse,
    SyncHistoryListResponse,
    TriggerSyncResponse,
    UpdateConnectorRequest,
)
from src.connectors.service import ConnectorService, get_connector_service
from src.core.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])

_arq_pool: ArqRedis | None = None


async def _get_arq_pool() -> ArqRedis:
    global _arq_pool
    if _arq_pool is None:
        from src.jobs.worker import _parse_redis_settings
        _arq_pool = await create_pool(_parse_redis_settings())
    return _arq_pool


def _get_service() -> ConnectorService:
    return get_connector_service()


# --- CRUD ---


@router.post("", status_code=201, response_model=ConnectorResponse)
async def create_connector(
    body: CreateConnectorRequest,
    db: TenantDB,
    user: CurrentUser,
    tenant_id: CurrentTenant,
):
    """Create a new connector."""
    # Check connector quota
    try:
        tier, _ = await get_tenant_billing_info(db, tenant_id)
        await enforce_connector_quota(db, tenant_id, tier)
    except QuotaExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "quota_type": exc.quota_type,
                "limit": exc.limit,
                "current": exc.current,
            },
        )

    service = _get_service()
    result = await service.create_connector(
        db=db,
        tenant_id=tenant_id,
        connector_type=body.connector_type,
        name=body.name,
        config=body.config or {},
        credentials=body.credentials,
        sync_frequency=body.sync_frequency or "daily",
        file_types=body.file_types,
        exclude_patterns=body.exclude_patterns,
    )
    await audit_log(
        db, tenant_id, user.user_id, "connector.create",
        "connector", result["id"],
    )
    return result


@router.get("", response_model=list[ConnectorResponse])
async def list_connectors(
    db: TenantDB,
    user: CurrentUser,
    tenant_id: CurrentTenant,
):
    """List all connectors for the current tenant."""
    service = _get_service()
    return await service.list_connectors(db, tenant_id)


@router.get("/{connector_id}", response_model=ConnectorResponse)
async def get_connector(
    connector_id: UUID,
    db: TenantDB,
    user: CurrentUser,
):
    """Get a single connector by ID."""
    service = _get_service()
    result = await service.get_connector(db, connector_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return result


@router.patch("/{connector_id}", response_model=ConnectorResponse)
async def update_connector(
    connector_id: UUID,
    body: UpdateConnectorRequest,
    db: TenantDB,
    user: CurrentUser,
    tenant_id: CurrentTenant,
):
    """Update a connector's settings."""
    service = _get_service()
    updates = body.model_dump(exclude_unset=True)
    result = await service.update_connector(db, connector_id, updates)
    if result is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    await audit_log(
        db, tenant_id, user.user_id, "connector.update",
        "connector", connector_id,
    )
    return result


@router.delete("/{connector_id}", status_code=204)
async def delete_connector(
    connector_id: UUID,
    db: TenantDB,
    user: CurrentUser,
    tenant_id: CurrentTenant,
):
    """Delete a connector and all associated data."""
    service = _get_service()
    deleted = await service.delete_connector(db, connector_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Connector not found")
    await audit_log(
        db, tenant_id, user.user_id, "connector.delete",
        "connector", connector_id,
    )


# --- Sync ---


@router.post("/{connector_id}/sync", response_model=TriggerSyncResponse)
async def trigger_sync(
    connector_id: UUID,
    db: TenantDB,
    user: CurrentUser,
    tenant_id: CurrentTenant,
):
    """Trigger an immediate sync for a connector."""
    service = _get_service()

    connector = await service.get_connector(db, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")

    await service.trigger_sync(db, connector_id)

    # Enqueue the ARQ job
    try:
        pool = await _get_arq_pool()
        await pool.enqueue_job("sync_connector", str(connector_id), str(tenant_id))
    except Exception as exc:
        logger.error("Failed to enqueue sync job: %s", exc)
        raise HTTPException(status_code=503, detail="Could not queue sync job")

    await audit_log(
        db, tenant_id, user.user_id, "connector.sync",
        "connector", connector_id,
    )
    return {"message": "Sync triggered", "connector_id": connector_id}


@router.get("/{connector_id}/history", response_model=SyncHistoryListResponse)
async def get_sync_history(
    connector_id: UUID,
    db: TenantDB,
    user: CurrentUser,
):
    """Get sync history for a connector."""
    service = _get_service()
    history = await service.get_sync_history(db, connector_id)
    return {"items": history, "total": len(history)}


# --- OAuth ---


@router.get("/oauth/{connector_type}/start", response_model=OAuthStartResponse)
async def oauth_start(
    connector_type: str,
    db: TenantDB,
    user: CurrentUser,
    tenant_id: CurrentTenant,
):
    """Start an OAuth flow for a connector type (e.g., google_drive)."""
    try:
        result = await start_oauth(
            tenant_id=tenant_id,
            connector_type=connector_type,
            name=f"{connector_type} connector",
            config={},
        )
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return result


@router.get("/oauth/callback")
async def oauth_callback(
    state: str,
    code: str,
    db: TenantDB,
    user: CurrentUser,
    tenant_id: CurrentTenant,
):
    """Handle OAuth callback — exchange code and create connector."""
    try:
        result = await complete_oauth(db, state, code)
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await audit_log(
        db, tenant_id, user.user_id, "connector.oauth_complete",
        "connector", result["id"],
    )
    return result
