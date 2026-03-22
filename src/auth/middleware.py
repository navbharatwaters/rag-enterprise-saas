import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from src.auth.api_keys import verify_api_key, update_api_key_usage
from src.auth.exceptions import ApiKeyError, JWTError, TenantNotFoundError
from src.auth.jwt import verify_clerk_jwt
from src.auth.models import ApiKeyUser, AuthenticatedUser
from src.auth.tenant import get_or_create_tenant, get_or_create_user, _get_admin_session_factory
from src.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Verifies Clerk JWT tokens or API keys and attaches user info to request.

    Behavior:
    - Skips public routes (health, docs, webhooks)
    - Checks X-API-Key header first (handled in API key task)
    - Falls back to Authorization: Bearer <jwt>
    - Returns 401 if no valid auth found
    - Attaches AuthenticatedUser to request.state.user
    """

    PUBLIC_PATHS = [
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/webhooks/",
    ]

    async def dispatch(self, request: Request, call_next):
        # Skip CORS preflight — CORSMiddleware handles these
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip public paths
        if self._is_public_path(request.url.path):
            return await call_next(request)

        # Check for API key first
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return await self._handle_api_key(request, call_next, api_key)

        # Check for JWT Bearer token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "Missing or invalid authorization token",
                    "code": "AUTH_MISSING_TOKEN",
                },
            )

        token = auth_header[7:]  # Strip "Bearer "

        try:
            claims = await verify_clerk_jwt(token)
        except JWTError as e:
            logger.warning("jwt_invalid error=%s", e)
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": str(e),
                    "code": "AUTH_INVALID_TOKEN",
                },
            )

        # Attach authenticated user to request state
        request.state.user = AuthenticatedUser(
            clerk_user_id=claims["sub"],
            clerk_org_id=claims["org_id"],
            org_role=claims.get("org_role", "org:member"),
            email=claims.get("user_email", ""),
        )

        return await call_next(request)

    def _is_public_path(self, path: str) -> bool:
        return any(path.startswith(p) for p in self.PUBLIC_PATHS)

    async def _handle_api_key(self, request: Request, call_next, api_key: str):
        """Verify API key and attach ApiKeyUser to request state."""
        try:
            factory = _get_admin_session_factory()
            async with factory() as db:
                async with db.begin():
                    key_record = await verify_api_key(db, api_key)
                    await update_api_key_usage(db, key_record.id)
        except ApiKeyError as e:
            logger.warning("api_key_invalid error=%s", e)
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": str(e),
                    "code": "AUTH_INVALID_API_KEY",
                },
            )
        except Exception as e:
            logger.error("api_key_error error=%s", e)
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_error",
                    "message": "API key verification failed",
                    "code": "AUTH_API_KEY_ERROR",
                },
            )

        # Attach API key user to request state
        scopes = key_record.scopes if isinstance(key_record.scopes, list) else []
        request.state.user = ApiKeyUser(
            api_key_id=key_record.id,
            tenant_id=key_record.tenant_id,
            scopes=scopes,
        )
        request.state.tenant_id = key_record.tenant_id

        return await call_next(request)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Resolves tenant from authenticated user and sets RLS context.

    Must run AFTER JWTAuthMiddleware in the middleware stack.
    Starlette processes middleware bottom-up, so add this BEFORE
    JWTAuthMiddleware when registering:

        app.add_middleware(TenantContextMiddleware)  # runs 2nd
        app.add_middleware(JWTAuthMiddleware)         # runs 1st

    Sets on request.state:
        - tenant_id: UUID of the resolved tenant
        - db: AsyncSession with RLS context already set
        - user.tenant_id: populated on AuthenticatedUser
        - user.user_id: populated with internal user ID
    """

    async def dispatch(self, request: Request, call_next):
        # Skip if no authenticated user (public paths already handled)
        if not hasattr(request.state, "user"):
            return await call_next(request)

        user = request.state.user

        # API key users already have tenant_id set by JWTAuthMiddleware
        if isinstance(user, ApiKeyUser):
            return await self._with_rls_session(request, call_next, user.tenant_id)

        # JWT users with tenant_id already resolved (shouldn't happen normally)
        if hasattr(user, "tenant_id") and user.tenant_id is not None:
            return await self._with_rls_session(request, call_next, user.tenant_id)

        try:
            # Resolve tenant from Clerk org ID (uses admin engine)
            tenant = await get_or_create_tenant(user.clerk_org_id)
        except Exception as e:
            logger.error("tenant_resolution_failed org_id=%s error=%s", user.clerk_org_id, e)
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_error",
                    "message": "Failed to resolve tenant",
                    "code": "TENANT_RESOLUTION_FAILED",
                },
            )

        # Resolve user record (uses admin engine)
        try:
            db_user = await get_or_create_user(
                tenant_id=tenant.id,
                clerk_user_id=user.clerk_user_id,
                clerk_org_role=user.org_role,
                email=user.email,
            )
        except Exception as e:
            logger.error("user_resolution_failed user=%s error=%s", user.clerk_user_id, e)
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_error",
                    "message": "Failed to resolve user",
                    "code": "USER_RESOLUTION_FAILED",
                },
            )

        # Populate user with internal IDs
        user.tenant_id = tenant.id
        user.user_id = db_user.id

        # Set tenant_id on request state for easy access
        request.state.tenant_id = tenant.id

        return await self._with_rls_session(request, call_next, tenant.id)

    async def _with_rls_session(self, request: Request, call_next, tenant_id):
        """Create a DB session with RLS context and attach to request."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
                )
                request.state.db = session
                response = await call_next(request)
        return response
