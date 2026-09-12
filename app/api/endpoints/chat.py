import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.auth import current_active_user
from app.core.db import get_async_session
from app.core.notifications import broadcast_notification
from app.models.chat import ChatMessage
from app.models.notification import Notification
from app.models.user import User
from app.schemas.chat import ChatMessageCreate, ChatMessageRead

router = APIRouter()


@router.get("/", response_model=List[ChatMessageRead])
async def get_messages(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    query = select(ChatMessage).where(
        (ChatMessage.recipient_id.is_(None))
        | (ChatMessage.sender_id == user.id)
        | (ChatMessage.recipient_id == user.id)
    ).order_by(ChatMessage.created_at.asc())
    result = await session.execute(query)
    messages = result.scalars().all()

    user_ids = {message.sender_id for message in messages}
    user_ids.update(message.recipient_id for message in messages if message.recipient_id)
    user_rows = await session.execute(select(User).where(User.id.in_(list(user_ids)))) if user_ids else None
    users = {}
    if user_rows:
        for row in user_rows.scalars().all():
            users[row.id] = row.username

    return [
        ChatMessageRead(
            id=message.id,
            content=message.content,
            sender_id=message.sender_id,
            recipient_id=message.recipient_id,
            created_at=message.created_at,
            sender_username=users.get(message.sender_id),
            message_type=message.message_type or "info",
            reference_title=message.reference_title,
            reference_url=message.reference_url,
        )
        for message in messages
    ]


@router.post("/", response_model=ChatMessageRead, status_code=status.HTTP_201_CREATED)
async def create_message(
    payload: ChatMessageCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El mensaje no puede ir vacío.")

    message_type = (payload.message_type or "info").strip().lower()
    allowed_types = {"info", "question", "report", "update", "alert"}
    if message_type not in allowed_types:
        message_type = "info"

    reference_title = payload.reference_title.strip() if payload.reference_title else None
    reference_url = payload.reference_url.strip() if payload.reference_url else None

    recipient = None
    if payload.recipient_id is not None:
        if payload.recipient_id == user.id:
            raise HTTPException(status_code=400, detail="No puedes enviarte un mensaje a ti mismo.")
        recipient = await session.get(User, payload.recipient_id)
        if recipient is None or not recipient.is_active:
            raise HTTPException(status_code=404, detail="Usuario destinatario no encontrado.")

    existing_user = await session.get(User, user.id)
    if existing_user is None:
        result = await session.execute(
            select(User).where(User.email == user.email)
        )
        existing_user = result.scalar_one_or_none()

    if existing_user is None:
        session.add(user)
        await session.flush()
        existing_user = user

    message = ChatMessage(
        content=content,
        sender_id=existing_user.id,
        recipient_id=recipient.id if recipient else None,
        message_type=message_type,
        reference_title=reference_title,
        reference_url=reference_url,
    )
    session.add(message)
    await session.flush()

    targets: list[User] = []
    if recipient is not None:
        targets = [recipient]
    else:
        result = await session.execute(
            select(User).where(User.is_active.is_(True), User.id != existing_user.id)
        )
        targets = result.scalars().all()

    for target_user in targets:
        preview = content.strip()
        if len(preview) > 90:
            preview = preview[:87] + "..."

        notification = Notification(
            user_id=str(target_user.id),
            title="Nuevo mensaje",
            message=f"{existing_user.username}: {preview}",
            type=message_type or "info",
            is_read=False,
        )
        session.add(notification)
        await session.flush()

        broadcast_notification(
            target_user.id,
            {
                "id": str(notification.id),
                "user_id": str(notification.user_id),
                "title": notification.title,
                "message": notification.message,
                "type": notification.type,
                "read": notification.read,
                "created_at": notification.created_at.isoformat(),
            },
        )

    await session.commit()
    await session.refresh(message)

    return ChatMessageRead(
        id=message.id,
        content=message.content,
        sender_id=message.sender_id,
        recipient_id=message.recipient_id,
        created_at=message.created_at,
        sender_username=existing_user.username,
        message_type=message.message_type,
        reference_title=message.reference_title,
        reference_url=message.reference_url,
    )


@router.get("/users")
async def list_chat_users(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    result = await session.execute(
        select(User).where(User.is_active.is_(True), User.id != user.id).order_by(User.username.asc())
    )
    return [
        {
            "id": str(item.id),
            "username": item.username,
            "nombre_completo": item.nombre_completo,
            "department": item.department,
        }
        for item in result.scalars().all()
    ]
