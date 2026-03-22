"""Stripe webhook event handlers."""

import json
import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.billing.constants import TIER_LIMITS, SubscriptionTier, get_tier_from_price

logger = logging.getLogger(__name__)


async def handle_subscription_created(
    db: AsyncSession,
    subscription: dict,
) -> None:
    """Handle customer.subscription.created event."""
    await _update_tenant_subscription(db, subscription)


async def handle_subscription_updated(
    db: AsyncSession,
    subscription: dict,
) -> None:
    """Handle customer.subscription.updated event."""
    await _update_tenant_subscription(db, subscription)


async def handle_subscription_deleted(
    db: AsyncSession,
    subscription: dict,
) -> None:
    """Handle customer.subscription.deleted — downgrade to free tier."""
    tenant_id = subscription.get("metadata", {}).get("tenant_id")
    if not tenant_id:
        logger.warning("Subscription deleted but no tenant_id in metadata")
        return

    await db.execute(
        text("""
            UPDATE tenants
            SET subscription_id = NULL,
                subscription_tier = 'free',
                subscription_status = 'canceled',
                limits = :limits
            WHERE id = :tenant_id
        """),
        {
            "tenant_id": tenant_id,
            "limits": json.dumps(TIER_LIMITS[SubscriptionTier.FREE]),
        },
    )
    await db.flush()
    logger.info("Tenant %s downgraded to free tier", tenant_id)


async def handle_payment_failed(
    db: AsyncSession,
    invoice: dict,
) -> None:
    """Handle invoice.payment_failed — mark tenant as past_due."""
    customer_id = invoice.get("customer")
    if not customer_id:
        logger.warning("Payment failed but no customer ID in invoice")
        return

    await db.execute(
        text("""
            UPDATE tenants
            SET subscription_status = 'past_due'
            WHERE stripe_customer_id = :customer_id
        """),
        {"customer_id": customer_id},
    )
    await db.flush()
    logger.info("Tenant with customer %s marked as past_due", customer_id)


async def _update_tenant_subscription(
    db: AsyncSession,
    subscription: dict,
) -> None:
    """Update tenant from subscription data."""
    tenant_id = subscription.get("metadata", {}).get("tenant_id")
    if not tenant_id:
        logger.warning("Subscription event but no tenant_id in metadata")
        return

    # Get tier from the first line item's price
    items = subscription.get("items", {}).get("data", [])
    if items:
        price_id = items[0].get("price", {}).get("id", "")
    else:
        price_id = ""

    tier = get_tier_from_price(price_id)

    period_end_ts = subscription.get("current_period_end")
    period_end = (
        datetime.fromtimestamp(period_end_ts) if period_end_ts else None
    )

    await db.execute(
        text("""
            UPDATE tenants
            SET subscription_id = :sub_id,
                subscription_tier = :tier,
                subscription_status = :status,
                current_period_end = :period_end,
                limits = :limits
            WHERE id = :tenant_id
        """),
        {
            "sub_id": subscription.get("id"),
            "tier": tier.value,
            "status": subscription.get("status", "active"),
            "period_end": period_end,
            "limits": json.dumps(TIER_LIMITS[tier]),
            "tenant_id": tenant_id,
        },
    )
    await db.flush()
    logger.info("Tenant %s updated to tier=%s status=%s", tenant_id, tier.value, subscription.get("status"))
