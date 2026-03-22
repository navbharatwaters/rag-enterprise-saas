class AuthError(Exception):
    """Base auth error."""
    pass


class JWTError(AuthError):
    """JWT verification failed."""
    pass


class TenantNotFoundError(AuthError):
    """Tenant doesn't exist."""
    pass


class InsufficientPermissionError(AuthError):
    """User lacks required role."""
    pass


class ApiKeyError(AuthError):
    """API key invalid or revoked."""
    pass


class RetryableError(Exception):
    """Webhook should be retried."""
    pass
