"""Billing service — business logic for billing operations."""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.billing.client import StripeClient, get_stripe_client
from src.billing.constants import TIER_LIMITS, SubscriptionStatus, SubscriptionTier
from src.billing.schemas import CurrentUsage, SubscriptionResponse, TierLimits

logger = logging.getLogger(__name__)


async def get_tenant_billing_info(
    db: AsyncSession,
    tenant_id: UUID,
) -> tuple[SubscriptionTier, str | None]:
    """Get tenant's subscription tier and Stripe customer ID.

    Returns (tier, stripe_customer_id). Used by quota and metering integrations.
    """
    result = await db.execute(
        text("SELECT subscription_tier, stripe_customer_id FROM tenants WHERE id = :id"),
        {"id": tenant_id},
    )
    tenant = result.fetchone()
    if not tenant:
        return SubscriptionTier.FREE, None

    tier = SubscriptionTier(tenant.subscription_tier or "free")
    return tier, tenant.stripe_customer_id


class BillingService:
    """High-level billing operations."""

    def __init__(self, stripe_client: StripeClient):
        self.stripe = stripe_client

    async def create_checkout(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        price_id: str,
        success_url: str,
        cancel_url: str,
    ) -> tuple[str, str]:
        """Create Stripe checkout session for a tenant."""
        result = await db.execute(
            text("SELECT stripe_customer_id FROM tenants WHERE id = :id"),
            {"id": tenant_id},
        )
        tenant = result.fetchone()

        if not tenant or not tenant.stripe_customer_id:
            raise ValueError("Tenant has no Stripe customer. Contact support.")

        return await self.stripe.create_checkout_session(
            customer_id=tenant.stripe_customer_id,
            price_id=price_id,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"tenant_id": str(tenant_id)},
        )

    async def create_portal_session(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        return_url: str,
    ) -> str:
        """Create Stripe billing portal session."""
        result = await db.execute(
            text("SELECT stripe_customer_id FROM tenants WHERE id = :id"),
            {"id": tenant_id},
        )
        tenant = result.fetchone()

        if not tenant or not tenant.stripe_customer_id:
            raise ValueError("Tenant has no Stripe customer. Contact support.")

        return await self.stripe.create_portal_session(
            customer_id=tenant.stripe_customer_id,
            return_url=return_url,
        )

    async def get_subscription(
        self,
        db: AsyncSession,
        tenant_id: UUID,
    ) -> SubscriptionResponse:
        """Get current subscription status with usage."""
        result = await db.execute(
            text("""
                SELECT
                    subscription_tier,
                    subscription_status,
                    current_period_end,
                    trial_ends_at,
                    limits
                FROM tenants WHERE id = :id
            """),
            {"id": tenant_id},
        )
        tenant = result.fetchone()

        if not tenant:
            raise ValueError("Tenant not found")

        tier = SubscriptionTier(tenant.subscription_tier or "free")
        status = SubscriptionStatus(tenant.subscription_status or "trialing")
        limits_dict = tenant.limits or TIER_LIMITS[tier]

        usage = await self._get_current_usage(db, tenant_id)

        return SubscriptionResponse(
            tier=tier,
            status=status,
            current_period_end=tenant.current_period_end,
            trial_ends_at=tenant.trial_ends_at,
            cancel_at_period_end=False,
            limits=TierLimits(**limits_dict),
            usage=usage,
        )

    async def _get_current_usage(
        self,
        db: AsyncSession,
        tenant_id: UUID,
    ) -> CurrentUsage:
        """Get current usage counts for a tenant."""
        queries_result = await db.execute(
            text("""
                SELECT COALESCE(SUM(quantity), 0) FROM usage_records
                WHERE tenant_id = :id AND usage_type = 'query_executed'
                  AND recorded_at >= date_trunc('month', CURRENT_DATE)
            """),
            {"id": tenant_id},
        )

        docs_result = await db.execute(
            text("""
                SELECT COUNT(*) FROM documents
                WHERE tenant_id = :id AND deleted_at IS NULL
            """),
            {"id": tenant_id},
        )

        storage_result = await db.execute(
            text("""
                SELECT COALESCE(SUM(file_size_bytes), 0) FROM documents
                WHERE tenant_id = :id AND deleted_at IS NULL
            """),
            {"id": tenant_id},
        )

        users_result = await db.execute(
            text("SELECT COUNT(*) FROM users WHERE tenant_id = :id"),
            {"id": tenant_id},
        )

        connectors_result = await db.execute(
            text("SELECT COUNT(*) FROM connectors WHERE tenant_id = :id"),
            {"id": tenant_id},
        )

        return CurrentUsage(
            queries_this_month=queries_result.scalar() or 0,
            documents_count=docs_result.scalar() or 0,
            storage_used_gb=(storage_result.scalar() or 0) / (1024**3),
            users_count=users_result.scalar() or 0,
            connectors_count=connectors_result.scalar() or 0,
        )


def get_billing_service() -> BillingService:
    """Get BillingService instance."""
    return BillingService(get_stripe_client())
