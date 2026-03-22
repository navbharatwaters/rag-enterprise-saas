"""Role-based access control for tenant operations.

Provides TenantRole enum and require_role() dependency factory
for protecting endpoints by role hierarchy.
"""

import enum
import functools
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from src.auth.models import AuthenticatedUser
from src.auth.dependencies import get_current_user


class TenantRole(enum.IntEnum):
    """Tenant roles ordered by privilege level (higher = more access)."""

    VIEWER = 0
    MEMBER = 1
    ADMIN = 2
    OWNER = 3


# Map internal role strings to TenantRole enum
ROLE_MAP: dict[str, TenantRole] = {
    "viewer": TenantRole.VIEWER,
    "member": TenantRole.MEMBER,
    "admin": TenantRole.ADMIN,
    "owner": TenantRole.OWNER,
}


def get_user_role(user: AuthenticatedUser) -> TenantRole:
    """Get the TenantRole for a user.

    Maps the string role (from DB/JWT) to the enum.
    Unknown roles default to VIEWER (least privilege).
    """
    # user.org_role comes from Clerk (e.g. "org:admin")
    # After tenant resolution, the internal role is on the user model
    # Check both the mapped internal role and fall back to org_role
    role_str = _normalize_role(user.org_role)
    return ROLE_MAP.get(role_str, TenantRole.VIEWER)


def _normalize_role(role: str) -> str:
    """Normalize Clerk or internal role string to internal format."""
    # Handle Clerk format "org:admin" -> "admin"
    if role.startswith("org:"):
        return role[4:]
    return role


def require_role(minimum_role: TenantRole):
    """FastAPI dependency factory that enforces minimum role.

    Usage:
        @router.delete("/documents/{id}")
        async def delete_doc(
            user: CurrentUser,
            _: Annotated[None, Depends(require_role(TenantRole.ADMIN))],
        ):
            ...

    Raises 403 if user's role is below the minimum required.
    """

    async def check_role(
        user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    ) -> None:
        user_role = get_user_role(user)
        if user_role < minimum_role:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "forbidden",
                    "message": f"Requires {minimum_role.name.lower()} role or higher",
                    "code": "AUTH_INSUFFICIENT_ROLE",
                    "required_role": minimum_role.name.lower(),
                    "current_role": user_role.name.lower(),
                },
            )

    return check_role
