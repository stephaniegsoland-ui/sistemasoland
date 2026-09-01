import asyncio
import json
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.db import get_async_session
from app.core.notifications import broadcast_notification, register_notification_queue, unregister_notification_queue
from app.models.notification import Notification
from app.schemas.notification import NotificationRead, NotificationCreate, NotificationUpdate
from app.core.auth import current_active_user
from app.models.user import User

router = APIRouter()


@router.get("/stream")
async def stream_notifications(
    request: Request,
    user: User = Depends(current_active_user),
):
    queue: asyncio.Queue = asyncio.Queue(maxsize=16)
    register_notification_queue(user.id, queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    notification = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                else:
                    yield f"data: {json.dumps(notification)}\n\n"
        finally:
            unregister_notification_queue(user.id, queue)

    return EventSourceResponse(event_generator())


@router.get("/", response_model=List[NotificationRead])
async def list_notifications(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    q = select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc())
    result = await session.execute(q)
    return result.scalars().all()


@router.post("/", response_model=NotificationRead, status_code=status.HTTP_201_CREATED)
async def create_notification(
    notification_in: NotificationCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    if notification_in.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No puedes crear notificaciones para otro usuario.")

    notification = Notification.model_validate(notification_in.model_dump())
    session.add(notification)
    await session.commit()
    await session.refresh(notification)
    return notification


@router.put("/{notification_id}", response_model=NotificationRead)
async def update_notification(
    notification_id: uuid.UUID,
    notification_in: NotificationUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    notification = await session.get(Notification, notification_id)
    if not notification or notification.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Notificación no encontrada.")

    update_data = notification_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(notification, key, value)
    session.add(notification)
    await session.commit()
    await session.refresh(notification)
    return notification
