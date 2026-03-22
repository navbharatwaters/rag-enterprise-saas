"""FastAPI dependencies for auth context.

Provides type-safe access to authenticated user, tenant, and
database session with RLS context already set.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import AuthenticatedUser


async def get_current_user(request: Request) -> AuthenticatedUser:
    """Get the authenticated user from request state.

    Raises 401 if request is not authenticated (middleware didn't run
    or this is an unauthenticated path that shouldn't reach here).
    """
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return request.state.user


async def get_current_tenant(request: Request) -> UUID:
    """Get the current tenant ID from request state.

    Raises 401 if tenant context is not set (middleware didn't resolve tenant).
    """
    if not hasattr(request.state, "tenant_id"):
        raise HTTPException(status_code=401, detail="No tenant context")
    return request.state.tenant_id


async def get_db_session(request: Request) -> AsyncSession:
    """Get a database session with RLS tenant context already set.

    The TenantContextMiddleware creates a session with
    SET LOCAL app.current_tenant_id already executed, so all
    queries through this session are tenant-scoped.

    Raises 401 if no DB session is available (not authenticated).
    """
    if not hasattr(request.state, "db"):
        raise HTTPException(status_code=401, detail="No database session")
    return request.state.db


# Type aliases for cleaner route signatures
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
CurrentTenant = Annotated[UUID, Depends(get_current_tenant)]
TenantDB = Annotated[AsyncSession, Depends(get_db_session)]
