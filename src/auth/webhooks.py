"""Clerk webhook handlers with Svix signature verification.

Handles organization and membership lifecycle events from Clerk.
"""

import logging

from svix.webhooks import Webhook, WebhookVerificationError

from src.auth.exceptions import RetryableError
from src.auth.tenant import _get_admin_session_factory
from src.core.config import settings
from src.models.tenant import Tenant, User

from sqlalchemy import select, update, delete

logger = logging.getLogger(__name__)


def verify_webhook_signature(payload: bytes, headers: dict[str, str]) -> dict:
    """Verify Svix webhook signature and return parsed payload.

    Args:
        payload: Raw request body bytes.
        headers: Request headers dict (needs svix-id, svix-timestamp, svix-signature).

    Returns:
        Parsed webhook payload dict.

    Raises:
        WebhookVerificationError: If signature is invalid.
    """
    wh = Webhook(settings.CLERK_WEBHOOK_SECRET)
    return wh.verify(payload, headers)


async def handle_webhook_event(event_type: str, data: dict) -> dict:
    """Route webhook event to appropriate handler.

    Returns:
        Dict with processing result.
    """
    handlers = {
        "organization.created": handle_organization_created,
        "organization.updated": handle_organization_updated,
        "organizationMembership.created": handle_membership_created,
        "organizationMembership.deleted": handle_membership_deleted,
    }

    handler = handlers.get(event_type)
    if handler is None:
        logger.info("webhook_ignored event_type=%s", event_type)
        return {"status": "ignored", "event_type": event_type}

    return await handler(data)


async def handle_organization_created(data: dict) -> dict:
    """Create or update tenant when a Clerk organization is created."""
    clerk_org_id = data["id"]
    name = data.get("name", clerk_org_id)
    slug = data.get("slug", clerk_org_id)

    factory = _get_admin_session_factory()
    async with factory() as db:
        async with db.begin():
            result = await db.execute(
                select(Tenant).where(Tenant.clerk_org_id == clerk_org_id)
            )
            tenant = result.scalar_one_or_none()

            if tenant:
                # Update name/slug if tenant was created by first-login flow
                tenant.name = name
                tenant.slug = slug
                logger.info("tenant_updated clerk_org_id=%s", clerk_org_id)
            else:
                tenant = Tenant(
                    clerk_org_id=clerk_org_id,
                    name=name,
                    slug=slug,
                    subscription_tier="starter",
                    subscription_status="trialing",
                )
                db.add(tenant)
                logger.info("tenant_created clerk_org_id=%s", clerk_org_id)

    return {"status": "processed", "action": "organization.created"}


async def handle_organization_updated(data: dict) -> dict:
    """Update tenant when a Clerk organization is updated."""
    clerk_org_id = data["id"]
    name = data.get("name")
    slug = data.get("slug")

    updates = {}
    if name is not None:
        updates["name"] = name
    if slug is not None:
        updates["slug"] = slug

    if not updates:
        return {"status": "no_changes"}

    factory = _get_admin_session_factory()
    async with factory() as db:
        async with db.begin():
            await db.execute(
                update(Tenant)
                .where(Tenant.clerk_org_id == clerk_org_id)
                .values(**updates)
            )
            logger.info("tenant_updated clerk_org_id=%s fields=%s", clerk_org_id, list(updates))

    return {"status": "processed", "action": "organization.updated"}


async def handle_membership_created(data: dict) -> dict:
    """Create or update user when added to a Clerk organization."""
    clerk_org_id = data.get("organization", {}).get("id")
    public_user_data = data.get("public_user_data", {})
    clerk_user_id = public_user_data.get("user_id") or data.get("public_user_data", {}).get("id")
    email = public_user_data.get("identifier", "")
    role = data.get("role", "org:member")

    if not clerk_org_id or not clerk_user_id:
        logger.warning("membership_created missing data: org=%s user=%s", clerk_org_id, clerk_user_id)
        return {"status": "error", "message": "Missing org or user ID"}

    factory = _get_admin_session_factory()
    async with factory() as db:
        async with db.begin():
            # Find tenant
            result = await db.execute(
                select(Tenant).where(Tenant.clerk_org_id == clerk_org_id)
            )
            tenant = result.scalar_one_or_none()

            if tenant is None:
                logger.warning("membership_created tenant_not_found org=%s", clerk_org_id)
                raise RetryableError(f"Tenant not found for org {clerk_org_id}")

            # Set tenant context for RLS
            from sqlalchemy import text
            await db.execute(
                text(f"SET LOCAL app.current_tenant_id = '{tenant.id}'")
            )

            # Upsert user
            result = await db.execute(
                select(User).where(
                    User.tenant_id == tenant.id,
                    User.clerk_user_id == clerk_user_id,
                )
            )
            user = result.scalar_one_or_none()

            role_str = role.replace("org:", "") if role.startswith("org:") else role

            if user:
                user.email = email
                user.role = role_str
                logger.info("user_updated clerk_user_id=%s", clerk_user_id)
            else:
                user = User(
                    tenant_id=tenant.id,
                    clerk_user_id=clerk_user_id,
                    email=email,
                    role=role_str,
                )
                db.add(user)
                logger.info("user_created clerk_user_id=%s tenant=%s", clerk_user_id, tenant.id)

    return {"status": "processed", "action": "membership.created"}


async def handle_membership_deleted(data: dict) -> dict:
    """Remove user when removed from a Clerk organization."""
    clerk_org_id = data.get("organization", {}).get("id")
    public_user_data = data.get("public_user_data", {})
    clerk_user_id = public_user_data.get("user_id") or public_user_data.get("id")

    if not clerk_org_id or not clerk_user_id:
        logger.warning("membership_deleted missing data: org=%s user=%s", clerk_org_id, clerk_user_id)
        return {"status": "error", "message": "Missing org or user ID"}

    factory = _get_admin_session_factory()
    async with factory() as db:
        async with db.begin():
            # Find tenant
            result = await db.execute(
                select(Tenant).where(Tenant.clerk_org_id == clerk_org_id)
            )
            tenant = result.scalar_one_or_none()

            if tenant is None:
                logger.info("membership_deleted tenant_not_found org=%s (may be deleted)", clerk_org_id)
                return {"status": "ignored", "message": "Tenant not found"}

            # Set tenant context for RLS
            from sqlalchemy import text
            await db.execute(
                text(f"SET LOCAL app.current_tenant_id = '{tenant.id}'")
            )

            # Delete user
            await db.execute(
                delete(User).where(
                    User.tenant_id == tenant.id,
                    User.clerk_user_id == clerk_user_id,
                )
            )
            logger.info("user_deleted clerk_user_id=%s tenant=%s", clerk_user_id, tenant.id)

    return {"status": "processed", "action": "membership.deleted"}
