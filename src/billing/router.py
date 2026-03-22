"""FastAPI router for billing endpoints."""

import logging

from fastapi import APIRouter, HTTPException, Request

from src.auth.dependencies import CurrentTenant, CurrentUser, TenantDB
from src.billing.client import get_stripe_client
from src.billing.schemas import (
    CheckoutRequest,
    CheckoutResponse,
    PortalRequest,
    PortalResponse,
    SubscriptionResponse,
)
from src.billing.service import BillingService, get_billing_service
from src.billing.webhooks import (
    handle_payment_failed,
    handle_subscription_created,
    handle_subscription_deleted,
    handle_subscription_updated,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


def _get_service() -> BillingService:
    return get_billing_service()


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    request: CheckoutRequest,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
):
    """Create Stripe checkout session for subscription upgrade."""
    service = _get_service()
    try:
        url, session_id = await service.create_checkout(
            db=db,
            tenant_id=tenant_id,
            price_id=request.price_id,
            success_url=request.success_url,
            cancel_url=request.cancel_url,
        )
        return CheckoutResponse(checkout_url=url, session_id=session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/portal", response_model=PortalResponse)
async def create_portal(
    request: PortalRequest,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
):
    """Create Stripe billing portal session."""
    service = _get_service()
    try:
        url = await service.create_portal_session(
            db=db,
            tenant_id=tenant_id,
            return_url=request.return_url,
        )
        return PortalResponse(portal_url=url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
):
    """Get current subscription status and usage."""
    service = _get_service()
    try:
        return await service.get_subscription(db, tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events.

    This endpoint does NOT use auth middleware — Stripe calls it directly.
    Security is via webhook signature verification.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    client = get_stripe_client()

    try:
        event = client.verify_webhook(payload, sig_header)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    # Webhooks bypass RLS — use admin session
    # The db session here comes from request.state set by middleware,
    # but for webhook events we pass the data dict to handlers that
    # use raw SQL with explicit WHERE clauses (no RLS needed).
    db = getattr(request.state, "db", None)

    if event_type == "customer.subscription.created":
        if db:
            await handle_subscription_created(db, data)
    elif event_type == "customer.subscription.updated":
        if db:
            await handle_subscription_updated(db, data)
    elif event_type == "customer.subscription.deleted":
        if db:
            await handle_subscription_deleted(db, data)
    elif event_type == "invoice.payment_failed":
        if db:
            await handle_payment_failed(db, data)
    else:
        logger.debug("Unhandled webhook event: %s", event_type)

    return {"status": "ok"}
