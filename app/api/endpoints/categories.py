from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

import uuid

from app.core.db import get_async_session
from app.models.inventory import Category
from app.schemas.inventary import CategoryCreate, CategoryRead
from app.core.auth import current_active_user
from app.models.user import User
from app.models.notification import Notification
from app.core.notifications import broadcast_notification

router = APIRouter()


@router.post("/", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(
    category_in: CategoryCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    query = select(Category).where(Category.name == category_in.name)
    result = await session.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe una categoria con ese nombre.",
        )
    new_category = Category.model_validate(category_in)
    session.add(new_category)
    await session.commit()
    await session.refresh(new_category)

    # Create a notification for all active users informing about the new category
    users_q = await session.execute(select(User).where(User.is_active == True))
    users = users_q.scalars().all()

    notifications = []
    for u in users:
        note = Notification(
            user_id=str(u.id),
            title="Nueva categoría creada",
            message=f"Se creó la categoría '{new_category.name}'.",
            type="info",
        )
        notifications.append(note)
        session.add(note)

    await session.commit()

    # Broadcast to connected users (SSE)
    for note in notifications:
        try:
            payload = {
                "id": note.id,
                "user_id": note.user_id,
                "title": note.title,
                "message": note.message,
                "type": note.type,
                "createdAt": note.created_at.isoformat(),
            }
            broadcast_notification(uuid.UUID(note.user_id), payload)
        except Exception:
            # ignore broadcast errors
            pass
    return new_category


@router.get("/", response_model=List[CategoryRead])
async def get_categories(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    result = await session.execute(select(Category))
    return result.scalars().all()


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: int,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    category = await session.get(Category, category_id)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Categoria no encontrada."
        )
    try:
        await session.delete(category)
        await session.commit()
    except:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se pudo eliminar la categoria proque aun tiene items en el inventario.",
        )
