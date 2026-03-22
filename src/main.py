"""RAG SaaS Platform - FastAPI Application."""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import from_url as redis_from_url

from src.auth.middleware import JWTAuthMiddleware, TenantContextMiddleware
from src.auth.router import router as auth_router
from src.api.v1.webhooks import router as webhooks_router
from src.cache.router import router as cache_router
from src.documents.router import router as documents_router
from src.documents.batch_router import router as batch_router
from src.retrieval.router import router as search_router
from src.generation.router import router as generation_router
from src.generation.conversation_router import router as conversation_router
from src.billing.router import router as billing_router
from src.connectors.router import router as connectors_router
from src.core.config import settings

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app-scoped resources (Redis connection pool)."""
    app.state.redis = redis_from_url(settings.REDIS_URL, decode_responses=True)
    logger.info("Redis connection pool created")
    yield
    await app.state.redis.aclose()
    logger.info("Redis connection pool closed")


app = FastAPI(
    title="RAG SaaS Platform",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
)


# --- Middleware Stack ---
# Starlette processes middleware bottom-up (last added = outermost = first to run).
# Execution order on request: CORS → JWT Auth → Tenant Context → Route


class RequestIDMiddleware:
    """Add unique request ID to each request for tracing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {})
        await self.app(scope, receive, send)


# 3rd (innermost): Tenant context
app.add_middleware(TenantContextMiddleware)

# 2nd: JWT auth
app.add_middleware(JWTAuthMiddleware)

# 1st (outermost): CORS — must be added LAST so it runs FIRST,
# intercepting OPTIONS preflight before auth middleware sees it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.navbharatwater.biz"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Routers ---
app.include_router(auth_router)
app.include_router(webhooks_router)
app.include_router(cache_router)
app.include_router(documents_router)
app.include_router(batch_router)
app.include_router(search_router)
app.include_router(generation_router)
app.include_router(conversation_router)
app.include_router(billing_router)
app.include_router(connectors_router)


# --- Health Check (public) ---


@app.get("/health", tags=["health"])
async def health_check():
    """Health check endpoint. No authentication required."""
    return {"status": "ok", "service": "rag-saas"}


# --- Global Exception Handler ---


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and return 500."""
    logger.error("unhandled_exception path=%s error=%s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred",
            "code": "INTERNAL_ERROR",
        },
    )
