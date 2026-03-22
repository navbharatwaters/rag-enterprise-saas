from uuid import UUID

from pydantic import BaseModel


class AuthenticatedUser(BaseModel):
    """User info attached to request.state.user after JWT verification."""

    clerk_user_id: str
    clerk_org_id: str
    org_role: str
    email: str

    # Populated after tenant resolution
    user_id: UUID | None = None
    tenant_id: UUID | None = None


class ApiKeyUser(BaseModel):
    """User info when authenticated via API key."""

    api_key_id: UUID
    tenant_id: UUID
    scopes: list[str]
