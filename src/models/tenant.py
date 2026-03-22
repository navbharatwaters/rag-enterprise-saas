import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, SoftDeleteMixin, TenantMixin, TimestampMixin


class Tenant(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    clerk_org_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    # Settings
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    # Billing
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    subscription_tier: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'starter'")
    )
    subscription_status: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'trialing'")
    )

    # Limits
    max_documents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1000"))
    max_queries_per_month: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("10000")
    )
    max_storage_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("10737418240")
    )

    # Relationships
    users: Mapped[list["User"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_tenants_clerk_org", "clerk_org_id"),
        Index(
            "idx_tenants_stripe",
            "stripe_customer_id",
            postgresql_where=text("stripe_customer_id IS NOT NULL"),
        ),
    )


class User(TenantMixin, TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    clerk_user_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Profile
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # Role
    role: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'member'"))

    # Activity
    last_active_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="users")

    __table_args__ = (
        UniqueConstraint("tenant_id", "clerk_user_id", name="uq_users_tenant_clerk"),
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index("idx_users_tenant", "tenant_id"),
        Index("idx_users_clerk", "clerk_user_id"),
        Index("idx_users_email", "tenant_id", "email"),
    )
