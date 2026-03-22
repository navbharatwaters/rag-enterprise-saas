"""Conversation management for the generation pipeline."""

import json
import logging
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings

logger = logging.getLogger(__name__)


async def create_conversation(
    db: AsyncSession,
    user_id: UUID,
    title: str | None = None,
) -> UUID:
    """Create a new conversation."""
    conv_id = uuid4()
    await db.execute(
        text("""
            INSERT INTO conversations (id, tenant_id, user_id, title)
            VALUES (:id, current_setting('app.current_tenant_id')::uuid, :user_id, :title)
        """),
        {"id": conv_id, "user_id": user_id, "title": title},
    )
    await db.flush()
    return conv_id


async def get_conversation(
    db: AsyncSession,
    conversation_id: UUID,
) -> dict | None:
    """Get conversation by ID (RLS enforces tenant isolation)."""
    result = await db.execute(
        text("""
            SELECT id, user_id, title, created_at, updated_at
            FROM conversations
            WHERE id = :id AND deleted_at IS NULL
        """),
        {"id": conversation_id},
    )
    row = result.fetchone()
    if not row:
        return None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "title": row.title,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_conversations(
    db: AsyncSession,
    user_id: UUID,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """List conversations for a user (RLS scoped)."""
    count_result = await db.execute(
        text("""
            SELECT COUNT(*) FROM conversations
            WHERE user_id = :user_id AND deleted_at IS NULL
        """),
        {"user_id": user_id},
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        text("""
            SELECT id, title, created_at, updated_at
            FROM conversations
            WHERE user_id = :user_id AND deleted_at IS NULL
            ORDER BY updated_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"user_id": user_id, "limit": limit, "offset": offset},
    )

    conversations = [
        {
            "id": row.id,
            "title": row.title,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in result.fetchall()
    ]
    return conversations, total


async def delete_conversation(
    db: AsyncSession,
    conversation_id: UUID,
) -> bool:
    """Soft-delete a conversation (RLS scoped)."""
    result = await db.execute(
        text("""
            UPDATE conversations
            SET deleted_at = NOW()
            WHERE id = :id AND deleted_at IS NULL
        """),
        {"id": conversation_id},
    )
    await db.flush()
    return result.rowcount > 0


async def get_conversation_history(
    db: AsyncSession,
    conversation_id: UUID,
    max_turns: int | None = None,
) -> list[dict[str, str]]:
    """Load recent conversation history for use in prompts.

    Returns messages in chronological order, limited to max_turns exchanges.
    """
    if max_turns is None:
        max_turns = settings.MAX_CONVERSATION_HISTORY

    result = await db.execute(
        text("""
            SELECT role, content
            FROM messages
            WHERE conversation_id = :conv_id
            ORDER BY created_at DESC
            LIMIT :limit
        """),
        {"conv_id": conversation_id, "limit": max_turns * 2},
    )

    messages = [{"role": r.role, "content": r.content} for r in result.fetchall()]
    return list(reversed(messages))


async def get_conversation_messages(
    db: AsyncSession,
    conversation_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Get full message details for a conversation."""
    result = await db.execute(
        text("""
            SELECT id, role, content, model, prompt_tokens, completion_tokens,
                   latency_ms, citations, feedback, created_at
            FROM messages
            WHERE conversation_id = :conv_id
            ORDER BY created_at ASC
            LIMIT :limit OFFSET :offset
        """),
        {"conv_id": conversation_id, "limit": limit, "offset": offset},
    )

    return [
        {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "model": row.model,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "latency_ms": row.latency_ms,
            "citations": row.citations,
            "feedback": row.feedback,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in result.fetchall()
    ]


async def save_message(
    db: AsyncSession,
    conversation_id: UUID,
    role: str,
    content: str,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
    citations: list[dict] | None = None,
) -> UUID:
    """Save a message to a conversation."""
    message_id = uuid4()
    await db.execute(
        text("""
            INSERT INTO messages (
                id, tenant_id, conversation_id, role, content,
                model, prompt_tokens, completion_tokens, latency_ms, citations
            ) VALUES (
                :id, current_setting('app.current_tenant_id')::uuid,
                :conv_id, :role, :content,
                :model, :prompt_tokens, :completion_tokens, :latency_ms, :citations
            )
        """),
        {
            "id": message_id,
            "conv_id": conversation_id,
            "role": role,
            "content": content,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": latency_ms,
            "citations": json.dumps(citations) if citations else None,
        },
    )
    await db.flush()
    return message_id
