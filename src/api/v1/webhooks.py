"""Webhook endpoints for Clerk events."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from svix.webhooks import WebhookVerificationError

from src.auth.exceptions import RetryableError
from src.auth.webhooks import verify_webhook_signature, handle_webhook_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post("/clerk")
async def clerk_webhook(request: Request):
    """Handle incoming Clerk webhooks.

    Verifies Svix signature, routes to appropriate handler.
    Returns 200 for all recognized events (even if unhandled)
    to prevent Clerk from retrying.
    """
    body = await request.body()
    headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }

    try:
        payload = verify_webhook_signature(body, headers)
    except WebhookVerificationError:
        logger.warning("webhook_signature_invalid")
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid webhook signature"},
        )

    event_type = payload.get("type", "")
    data = payload.get("data", {})

    try:
        result = await handle_webhook_event(event_type, data)
    except RetryableError as e:
        logger.warning("webhook_retryable event=%s error=%s", event_type, e)
        return JSONResponse(
            status_code=503,
            content={"error": "Temporary failure, please retry"},
        )
    except Exception as e:
        logger.error("webhook_error event=%s error=%s", event_type, e, exc_info=True)
        return JSONResponse(
            status_code=200,
            content={"status": "error", "message": "Internal error processing webhook"},
        )

    return JSONResponse(status_code=200, content=result)
