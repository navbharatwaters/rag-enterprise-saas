"""Tenant resolution logic for multi-tenancy.

Resolves Clerk org_id to internal tenant, creating if needed.
Uses admin engine for lookups since RLS is not yet set at this point.
"""

import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import settings
from src.models.tenant import Tenant, User

logger = logging.getLogger(__name__)

# Lazy-initialized admin engine (avoids event loop issues in tests)
_admin_engine = None
_admin_session_factory = None


def _get_admin_session_factory() -> async_sessionmaker:
    """Get or create admin session factory (lazy init)."""
    global _admin_engine, _admin_session_factory
    if _admin_session_factory is None:
        _admin_engine = create_async_engine(
            settings.DATABASE_ADMIN_URL,
            echo=settings.APP_DEBUG,
            pool_size=5,
            max_overflow=5,
        )
        _admin_session_factory = async_sessionmaker(
            _admin_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _admin_session_factory


async def get_or_create_tenant(clerk_org_id: str) -> Tenant:
    """Get tenant by Clerk org ID, or create on first login.

    Uses admin engine because RLS context is not yet set.
    This handles the race condition where a user logs in before
    the Clerk webhook creates the tenant.

    Returns:
        Tenant model instance (detached from session).
    """
    async with _get_admin_session_factory()() as db:
        async with db.begin():
            result = await db.execute(
                select(Tenant).where(Tenant.clerk_org_id == clerk_org_id)
            )
            tenant = result.scalar_one_or_none()

            if tenant:
                return tenant

            # First login for this org - create tenant
            logger.info("tenant_create clerk_org_id=%s source=first_login", clerk_org_id)
            tenant = Tenant(
                clerk_org_id=clerk_org_id,
                name=clerk_org_id,  # Updated by webhook later
                slug=_generate_slug(clerk_org_id),
                subscription_tier="starter",
                subscription_status="trialing",
            )
            db.add(tenant)
            await db.flush()
            await db.refresh(tenant)
            return tenant


async def get_or_create_user(
    tenant_id: uuid.UUID,
    clerk_user_id: str,
    clerk_org_role: str,
    email: str,
) -> User:
    """Get or create user record for this tenant.

    Uses admin engine because RLS context is not yet set.
    """
    async with _get_admin_session_factory()() as db:
        async with db.begin():
            # Need to set tenant context for the users table (has RLS)
            await db.execute(
                text(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
            )

            result = await db.execute(
                select(User).where(
                    User.tenant_id == tenant_id,
                    User.clerk_user_id == clerk_user_id,
                )
            )
            user = result.scalar_one_or_none()

            if user:
                # Update role/email if changed
                role = _map_clerk_role(clerk_org_role)
                if user.role != role or user.email != email:
                    user.role = role
                    user.email = email
                    await db.flush()
                return user

            # Create new user
            logger.info(
                "user_create clerk_user_id=%s tenant_id=%s",
                clerk_user_id,
                tenant_id,
            )
            user = User(
                tenant_id=tenant_id,
                clerk_user_id=clerk_user_id,
                email=email,
                role=_map_clerk_role(clerk_org_role),
            )
            db.add(user)
            await db.flush()
            await db.refresh(user)
            return user


def _generate_slug(clerk_org_id: str) -> str:
    """Generate a URL-safe slug from clerk org ID."""
    # Strip 'org_' prefix and use as slug base
    base = clerk_org_id.replace("org_", "").lower()[:40]
    # Add short UUID suffix to avoid collisions
    suffix = uuid.uuid4().hex[:6]
    return f"{base}-{suffix}"


def _map_clerk_role(clerk_role: str) -> str:
    """Map Clerk org role to internal role.

    Clerk roles: org:admin, org:member
    Internal roles: owner, admin, member, viewer
    """
    mapping = {
        "org:admin": "admin",
        "org:member": "member",
    }
    return mapping.get(clerk_role, "member")
