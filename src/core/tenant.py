import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import AsyncSessionLocal


@asynccontextmanager
async def tenant_scope(db: AsyncSession, tenant_id: uuid.UUID) -> AsyncGenerator[None, None]:
    """Set tenant context for RLS within the current transaction.

    Must be used inside an active transaction (async with db.begin()).
    SET LOCAL automatically resets at transaction end.

    Raises ValueError if tenant_id is not a valid UUID.
    """
    if not isinstance(tenant_id, uuid.UUID):
        try:
            tenant_id = uuid.UUID(str(tenant_id))
        except (ValueError, AttributeError) as e:
            raise ValueError(f"Invalid tenant_id: {tenant_id}") from e

    await db.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_id}'"))
    yield


@asynccontextmanager
async def get_db_with_tenant(tenant_id: uuid.UUID) -> AsyncGenerator[AsyncSession, None]:
    """Get a database session with tenant context already set.

    Creates a new session, begins a transaction, sets tenant context,
    and yields the session. Commits on success, rolls back on error.

    Raises ValueError if tenant_id is not a valid UUID.
    """
    if not isinstance(tenant_id, uuid.UUID):
        try:
            tenant_id = uuid.UUID(str(tenant_id))
        except (ValueError, AttributeError) as e:
            raise ValueError(f"Invalid tenant_id: {tenant_id}") from e

    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_id}'"))
            yield session
