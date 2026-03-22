from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import settings

# Admin engine for migrations (rag_admin role, owns tables)
admin_engine = create_async_engine(
    settings.DATABASE_ADMIN_URL,
    echo=settings.APP_DEBUG,
)

# App engine for application queries (rag_app role, subject to RLS)
app_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_DEBUG,
    pool_size=20,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    app_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncSession:
    """Get an async database session using the app role."""
    async with AsyncSessionLocal() as session:
        yield session
