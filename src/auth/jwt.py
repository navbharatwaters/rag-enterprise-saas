import logging
from datetime import datetime, timedelta

import httpx
import jwt
from jwt import PyJWKClient, PyJWK

from src.auth.exceptions import JWTError
from src.core.config import settings

logger = logging.getLogger(__name__)


class JWKSClient:
    """Cached JWKS client for Clerk JWT verification."""

    def __init__(self, issuer: str, cache_ttl_minutes: int = 5):
        self.issuer = issuer
        self.jwks_url = f"{issuer}/.well-known/jwks.json"
        self._cache: dict | None = None
        self._cache_expires: datetime | None = None
        self._cache_ttl = timedelta(minutes=cache_ttl_minutes)

    async def get_signing_key(self, kid: str) -> dict:
        """Get signing key by key ID, with caching."""
        if self._cache_expired():
            await self._refresh_cache()

        key = self._find_key(kid)
        if key:
            return key

        # Key not found, force refresh once (key rotation)
        await self._refresh_cache()
        key = self._find_key(kid)
        if key:
            return key

        raise JWTError(f"Unknown signing key ID: {kid}")

    def _find_key(self, kid: str) -> dict | None:
        if not self._cache:
            return None
        for key in self._cache.get("keys", []):
            if key.get("kid") == kid:
                return key
        return None

    def _cache_expired(self) -> bool:
        return (
            self._cache is None
            or self._cache_expires is None
            or datetime.utcnow() > self._cache_expires
        )

    async def _refresh_cache(self) -> None:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.jwks_url, timeout=10.0)
                response.raise_for_status()
                self._cache = response.json()
                self._cache_expires = datetime.utcnow() + self._cache_ttl
                logger.debug("JWKS cache refreshed from %s", self.jwks_url)
        except httpx.HTTPError as e:
            if self._cache is not None:
                # Use stale cache if refresh fails
                logger.warning("JWKS refresh failed, using stale cache: %s", e)
                return
            raise JWTError(f"Failed to fetch JWKS: {e}") from e


# Global JWKS client instance
_jwks_client: JWKSClient | None = None


def get_jwks_client() -> JWKSClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = JWKSClient(settings.CLERK_JWT_ISSUER)
    return _jwks_client


async def verify_clerk_jwt(token: str) -> dict:
    """Verify a Clerk JWT and return its claims.

    Args:
        token: The raw JWT string (without "Bearer " prefix).

    Returns:
        Dict of JWT claims including sub, org_id, org_role, user_email.

    Raises:
        JWTError: If the token is invalid, expired, or has wrong issuer.
    """
    try:
        # Decode header to get kid
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid:
            raise JWTError("JWT missing key ID (kid) in header")

        # Get the signing key
        client = get_jwks_client()
        jwk_data = await client.get_signing_key(kid)
        public_key = PyJWK(jwk_data).key

        # Verify and decode
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=settings.CLERK_JWT_ISSUER,
            options={
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": False,  # Clerk doesn't always set aud
            },
        )

        # Validate required custom claims
        if "sub" not in claims:
            raise JWTError("JWT missing required claim: sub")

        # org_id is optional — personal sessions won't have it.
        # Inject fallback so callers always see a consistent org_id.
        if "org_id" not in claims:
            claims["org_id"] = f"user_{claims['sub']}"
        logger.info("jwt_verified user_id=%s org_id=%s", claims["sub"], claims["org_id"])
        return claims

    except jwt.ExpiredSignatureError as e:
        raise JWTError("JWT has expired") from e
    except jwt.InvalidIssuerError as e:
        raise JWTError("JWT has invalid issuer") from e
    except jwt.InvalidSignatureError as e:
        raise JWTError("JWT has invalid signature") from e
    except jwt.DecodeError as e:
        raise JWTError(f"JWT decode error: {e}") from e
    except jwt.PyJWTError as e:
        raise JWTError(f"JWT verification failed: {e}") from e
