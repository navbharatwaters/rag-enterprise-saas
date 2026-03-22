from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class EmbeddingCache(Base):
    """Cache for embeddings to avoid recomputation. No RLS - shared across tenants."""

    __tablename__ = "embedding_cache"

    text_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    embedding = mapped_column(Vector(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
