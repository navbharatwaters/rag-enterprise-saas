"""FastAPI router for conversation management."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from uuid import UUID

from src.auth.dependencies import CurrentTenant, CurrentUser, TenantDB
from src.generation.conversations import (
    create_conversation,
    delete_conversation,
    get_conversation,
    get_conversation_messages,
    list_conversations,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=500)


class ConversationResponse(BaseModel):
    id: UUID
    title: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    total: int


class ConversationDetailResponse(BaseModel):
    id: UUID
    user_id: UUID
    title: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    messages: list[dict]


@router.post("", response_model=ConversationResponse, status_code=201)
async def create(
    request: CreateConversationRequest,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
):
    """Create a new conversation."""
    conv_id = await create_conversation(
        db=db,
        user_id=user.user_id,
        title=request.title,
    )
    return ConversationResponse(id=conv_id, title=request.title)


@router.get("", response_model=ConversationListResponse)
async def list_all(
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
    limit: int = 20,
    offset: int = 0,
):
    """List conversations for the current user."""
    conversations, total = await list_conversations(
        db=db, user_id=user.user_id, limit=limit, offset=offset
    )
    return ConversationListResponse(
        conversations=[ConversationResponse(**c) for c in conversations],
        total=total,
    )


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get(
    conversation_id: UUID,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
):
    """Get conversation with messages."""
    conv = await get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await get_conversation_messages(db, conversation_id)
    return ConversationDetailResponse(**conv, messages=messages)


@router.delete("/{conversation_id}", status_code=204)
async def delete(
    conversation_id: UUID,
    user: CurrentUser,
    tenant_id: CurrentTenant,
    db: TenantDB,
):
    """Delete a conversation (soft delete)."""
    deleted = await delete_conversation(db, conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
