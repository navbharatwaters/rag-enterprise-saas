"""Stripe API client wrapper."""

import logging

import stripe

from src.core.config import settings

logger = logging.getLogger(__name__)


class StripeClient:
    """Wrapper for Stripe API operations."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or settings.STRIPE_SECRET_KEY
        stripe.api_key = self._api_key

    async def create_customer(
        self,
        email: str,
        name: str,
        metadata: dict,
    ) -> str:
        """Create Stripe customer, return customer ID."""
        customer = stripe.Customer.create(
            email=email,
            name=name,
            metadata=metadata,
        )
        return customer.id

    async def create_checkout_session(
        self,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        metadata: dict,
    ) -> tuple[str, str]:
        """Create checkout session, return (url, session_id)."""
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            subscription_data={"metadata": metadata},
            allow_promotion_codes=True,
        )
        return session.url, session.id

    async def create_portal_session(
        self,
        customer_id: str,
        return_url: str,
    ) -> str:
        """Create billing portal session, return URL."""
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return session.url

    async def get_subscription(self, subscription_id: str) -> dict:
        """Get subscription details."""
        sub = stripe.Subscription.retrieve(subscription_id)
        return dict(sub)

    async def cancel_subscription(
        self,
        subscription_id: str,
        at_period_end: bool = True,
    ) -> None:
        """Cancel subscription."""
        stripe.Subscription.modify(
            subscription_id,
            cancel_at_period_end=at_period_end,
        )

    async def record_meter_event(
        self,
        event_name: str,
        customer_id: str,
        value: int,
        timestamp: int | None = None,
    ) -> None:
        """Record usage event to Stripe Meter."""
        params: dict = {
            "event_name": event_name,
            "payload": {
                "stripe_customer_id": customer_id,
                "value": str(value),
            },
        }
        if timestamp:
            params["timestamp"] = timestamp
        stripe.billing.MeterEvent.create(**params)

    def verify_webhook(
        self,
        payload: bytes,
        signature: str,
        secret: str = "",
    ) -> dict:
        """Verify and parse webhook event."""
        webhook_secret = secret or settings.STRIPE_WEBHOOK_SECRET
        return stripe.Webhook.construct_event(
            payload,
            signature,
            webhook_secret,
        )


_stripe_client: StripeClient | None = None


def get_stripe_client() -> StripeClient:
    """Get singleton StripeClient instance."""
    global _stripe_client
    if _stripe_client is None:
        _stripe_client = StripeClient()
    return _stripe_client
