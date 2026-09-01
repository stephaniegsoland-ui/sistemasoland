from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.auth import current_active_user
from app.core.db import get_async_session
from app.models.chat import ChatMessage
from app.models.user import User
from app.schemas.chat import ChatMessageCreate, ChatMessageRead

router = APIRouter()


@router.get("/", response_model=List[ChatMessageRead])
async def get_messages(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    query = (
        select(ChatMessage)
        .order_by(ChatMessage.created_at.asc())
    )
    result = await session.execute(query)
    messages = result.scalars().all()

    users = {}
    for message in messages:
        user_result = await session.get(User, message.sender_id)
        if user_result:
            users[message.sender_id] = user_result.username

    return [
        ChatMessageRead(
            id=message.id,
            content=message.content,
            sender_id=message.sender_id,
            created_at=message.created_at,
            sender_username=users.get(message.sender_id),
        )
        for message in messages
    ]


@router.post("/", response_model=ChatMessageRead, status_code=status.HTTP_201_CREATED)
async def create_message(
    payload: ChatMessageCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    message = ChatMessage(content=payload.content.strip(), sender_id=user.id)
    session.add(message)
    await session.commit()
    await session.refresh(message)

    return ChatMessageRead(
        id=message.id,
        content=message.content,
        sender_id=message.sender_id,
        created_at=message.created_at,
        sender_username=user.username,
    )
