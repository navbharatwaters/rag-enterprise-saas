"""Document processing status webhook dispatcher.

Sends signed HTTP POST notifications to a configured endpoint whenever a
document transitions between processing states (processing, completed, failed).

Webhook signature:
    Header: X-Webhook-Signature: sha256=<hex>
    The HMAC-SHA256 is computed over the raw JSON body using SECRET_KEY.
    Receivers should verify this before trusting the payload.
"""

import hashlib
import hmac
import json
import logging
import time

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)


async def send_document_webhook(
    event: str,
    document_id: str,
    tenant_id: str,
    data: dict,
) -> None:
    """Fire a webhook notification for a document status change.

    Failures are logged and silently swallowed so they never block document
    processing. Configure DOCUMENT_WEBHOOK_URL to enable delivery.

    Args:
        event: Event name, e.g. "document.processing", "document.completed",
               "document.failed".
        document_id: Document UUID as string.
        tenant_id: Tenant UUID as string.
        data: Additional event-specific fields merged into the payload.
    """
    url = settings.DOCUMENT_WEBHOOK_URL
    if not url:
        return

    payload = {
        "event": event,
        "document_id": document_id,
        "tenant_id": tenant_id,
        "timestamp": int(time.time()),
        **data,
    }

    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(
        settings.SECRET_KEY.encode(), body, hashlib.sha256
    ).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": f"sha256={signature}",
                    "X-Webhook-Event": event,
                },
            )
            response.raise_for_status()
        logger.debug(
            "Webhook delivered: event=%s document=%s", event, document_id
        )
    except Exception as exc:
        logger.warning(
            "Webhook delivery failed: event=%s document=%s error=%s",
            event,
            document_id,
            exc,
        )
