"""Auth API endpoints for user info and API key management."""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.api_keys import generate_api_key
from src.auth.dependencies import CurrentUser, CurrentTenant, TenantDB
from src.auth.permissions import TenantRole, require_role
from src.core.audit import audit_log
from src.models.api_key import ApiKey

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# --- Schemas ---


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    scopes: list[str] = Field(default=["read", "write"])
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ApiKeyCreateResponse(BaseModel):
    id: UUID
    key: str  # Full key, shown only once
    key_prefix: str
    name: str
    scopes: list[str]
    expires_at: datetime | None
    created_at: datetime


class ApiKeyListItem(BaseModel):
    id: UUID
    key_prefix: str
    name: str
    scopes: list[str]
    last_used_at: datetime | None
    total_requests: int
    expires_at: datetime | None
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyListResponse(BaseModel):
    api_keys: list[ApiKeyListItem]


class MeResponse(BaseModel):
    user_id: UUID | None
    tenant_id: UUID | None
    clerk_user_id: str
    clerk_org_id: str
    org_role: str
    email: str


# --- Endpoints ---


@router.get("/me", response_model=MeResponse)
async def get_me(user: CurrentUser):
    """Get current authenticated user info."""
    return MeResponse(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        clerk_user_id=user.clerk_user_id,
        clerk_org_id=user.clerk_org_id,
        org_role=user.org_role,
        email=user.email,
    )


@router.post(
    "/api-keys",
    response_model=ApiKeyCreateResponse,
    status_code=201,
)
async def create_api_key(
    body: ApiKeyCreateRequest,
    request: Request,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
    _: Annotated[None, Depends(require_role(TenantRole.ADMIN))],
):
    """Create a new API key. Requires admin role.

    The full key is returned only once in the response.
    Store it securely - it cannot be retrieved again.
    """
    full_key, key_hash, key_prefix = generate_api_key()

    expires_at = None
    if body.expires_in_days is not None:
        from datetime import timedelta

        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    api_key = ApiKey(
        tenant_id=tenant_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=body.name,
        scopes=body.scopes,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.flush()

    await audit_log(
        db=db,
        tenant_id=tenant_id,
        action="api_key.create",
        resource_type="api_key",
        user_id=user.user_id,
        resource_id=api_key.id,
        details={"name": body.name, "scopes": body.scopes},
        request=request,
    )

    return ApiKeyCreateResponse(
        id=api_key.id,
        key=full_key,
        key_prefix=key_prefix,
        name=api_key.name,
        scopes=api_key.scopes,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
    )


@router.get("/api-keys", response_model=ApiKeyListResponse)
async def list_api_keys(
    user: CurrentUser,
    db: TenantDB,
    _: Annotated[None, Depends(require_role(TenantRole.ADMIN))],
):
    """List all API keys for the current tenant. Requires admin role.

    Keys are returned without the full key value (only prefix).
    """
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()

    return ApiKeyListResponse(
        api_keys=[
            ApiKeyListItem(
                id=k.id,
                key_prefix=k.key_prefix,
                name=k.name,
                scopes=k.scopes,
                last_used_at=k.last_used_at,
                total_requests=k.total_requests,
                expires_at=k.expires_at,
                created_at=k.created_at,
                revoked_at=k.revoked_at,
            )
            for k in keys
        ]
    )


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: UUID,
    request: Request,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
    _: Annotated[None, Depends(require_role(TenantRole.ADMIN))],
):
    """Revoke an API key. Requires admin role.

    Sets revoked_at timestamp. The key will no longer authenticate requests.
    """
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id)
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(status_code=404, detail="API key not found")

    if api_key.revoked_at is not None:
        raise HTTPException(status_code=409, detail="API key already revoked")

    await db.execute(
        update(ApiKey)
        .where(ApiKey.id == key_id)
        .values(revoked_at=datetime.now(timezone.utc))
    )

    await audit_log(
        db=db,
        tenant_id=tenant_id,
        action="api_key.revoke",
        resource_type="api_key",
        user_id=user.user_id,
        resource_id=key_id,
        details={"name": api_key.name},
        request=request,
    )
