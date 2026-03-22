"""Billing constants: tiers, limits, and price mappings."""

from enum import Enum

from src.core.config import settings


class SubscriptionTier(str, Enum):
    """Subscription tier levels."""

    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, Enum):
    """Subscription lifecycle status."""

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    PAUSED = "paused"


# Tier limits configuration
TIER_LIMITS: dict[SubscriptionTier, dict] = {
    SubscriptionTier.FREE: {
        "queries_per_month": 100,
        "documents": 10,
        "storage_gb": 0.1,
        "users": 2,
        "connectors": 0,
    },
    SubscriptionTier.STARTER: {
        "queries_per_month": 1000,
        "documents": 100,
        "storage_gb": 1,
        "users": 5,
        "connectors": 1,
    },
    SubscriptionTier.PROFESSIONAL: {
        "queries_per_month": 10000,
        "documents": 1000,
        "storage_gb": 10,
        "users": 25,
        "connectors": 5,
    },
    SubscriptionTier.ENTERPRISE: {
        "queries_per_month": -1,
        "documents": -1,
        "storage_gb": -1,
        "users": -1,
        "connectors": -1,
    },
}


def get_price_to_tier_mapping() -> dict[str, SubscriptionTier]:
    """Build price ID to tier mapping from settings."""
    return {
        settings.STRIPE_PRICE_STARTER: SubscriptionTier.STARTER,
        settings.STRIPE_PRICE_PROFESSIONAL: SubscriptionTier.PROFESSIONAL,
        settings.STRIPE_PRICE_ENTERPRISE: SubscriptionTier.ENTERPRISE,
    }


def get_tier_from_price(price_id: str) -> SubscriptionTier:
    """Get subscription tier for a Stripe price ID."""
    mapping = get_price_to_tier_mapping()
    return mapping.get(price_id, SubscriptionTier.STARTER)
