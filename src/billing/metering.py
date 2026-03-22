"""Usage metering — local recording and Stripe meter events."""

import logging
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.billing.client import get_stripe_client

logger = logging.getLogger(__name__)


async def record_usage(
    db: AsyncSession,
    tenant_id: UUID,
    stripe_customer_id: str | None,
    event_name: str,
    quantity: int = 1,
    timestamp: datetime | None = None,
) -> None:
    """Record usage event both locally and to Stripe.

    Always records locally. Sends to Stripe if customer exists.
    Stripe failures are logged but don't fail the operation.
    """
    ts = timestamp or datetime.utcnow()

    # Always record locally
    await db.execute(
        text("""
            INSERT INTO usage_records (id, tenant_id, usage_type, quantity, recorded_at)
            VALUES (:id, :tenant_id, :usage_type, :quantity, :recorded_at)
        """),
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "usage_type": event_name,
            "quantity": quantity,
            "recorded_at": ts,
        },
    )
    await db.flush()

    # Send to Stripe if customer exists
    if stripe_customer_id:
        try:
            client = get_stripe_client()
            await client.record_meter_event(
                event_name=event_name,
                customer_id=stripe_customer_id,
                value=quantity,
                timestamp=int(ts.timestamp()),
            )
        except Exception:
            logger.warning(
                "Failed to record Stripe meter event for tenant %s",
                tenant_id,
                exc_info=True,
            )


async def get_monthly_usage(
    db: AsyncSession,
    tenant_id: UUID,
    usage_type: str,
) -> int:
    """Get usage count for current calendar month."""
    result = await db.execute(
        text("""
            SELECT COALESCE(SUM(quantity), 0) as total
            FROM usage_records
            WHERE tenant_id = :tenant_id
              AND usage_type = :usage_type
              AND recorded_at >= date_trunc('month', CURRENT_DATE)
        """),
        {"tenant_id": tenant_id, "usage_type": usage_type},
    )
    return result.scalar() or 0


async def get_usage_summary(
    db: AsyncSession,
    tenant_id: UUID,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, int]:
    """Get usage summary grouped by type for a period."""
    result = await db.execute(
        text("""
            SELECT
                usage_type,
                COALESCE(SUM(quantity), 0) as total
            FROM usage_records
            WHERE tenant_id = :tenant_id
              AND recorded_at >= :start
              AND recorded_at < :end
            GROUP BY usage_type
        """),
        {
            "tenant_id": tenant_id,
            "start": period_start,
            "end": period_end,
        },
    )
    return {row.usage_type: row.total for row in result.fetchall()}
