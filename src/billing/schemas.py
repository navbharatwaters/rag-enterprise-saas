"""Pydantic models for billing API."""

from datetime import datetime

from pydantic import BaseModel, Field

from src.billing.constants import SubscriptionStatus, SubscriptionTier


class CheckoutRequest(BaseModel):
    """Request to create Stripe checkout session."""

    price_id: str
    success_url: str
    cancel_url: str


class CheckoutResponse(BaseModel):
    """Checkout session created."""

    checkout_url: str
    session_id: str


class PortalRequest(BaseModel):
    """Request for Stripe billing portal."""

    return_url: str


class PortalResponse(BaseModel):
    """Billing portal URL."""

    portal_url: str


class TierLimits(BaseModel):
    """Limits for a subscription tier."""

    queries_per_month: int
    documents: int
    storage_gb: float
    users: int
    connectors: int


class CurrentUsage(BaseModel):
    """Current usage for billing period."""

    queries_this_month: int
    documents_count: int
    storage_used_gb: float
    users_count: int
    connectors_count: int


class SubscriptionResponse(BaseModel):
    """Current subscription status with usage."""

    tier: SubscriptionTier
    status: SubscriptionStatus
    current_period_end: datetime | None = None
    trial_ends_at: datetime | None = None
    cancel_at_period_end: bool = False
    limits: TierLimits
    usage: CurrentUsage


class UsageResponse(BaseModel):
    """Usage for a billing period."""

    period_start: datetime
    period_end: datetime
    queries: int
    documents_uploaded: int
    storage_gb: float
    estimated_cost: float


class UsageEvent(BaseModel):
    """Usage event to record."""

    event_name: str = Field(..., pattern="^(query_executed|document_uploaded|storage_used)$")
    quantity: int = Field(default=1, ge=1)
    timestamp: datetime | None = None
