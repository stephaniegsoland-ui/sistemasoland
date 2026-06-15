from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typing import List

from app.core.db import get_async_session
from app.models.inventory import ItemInventary, Category
from app.schemas.inventary import ItemInventaryRead, ItemInventaryCreate
from app.core.auth import current_active_user
from app.models.user import User

router = APIRouter()


@router.post(
    "/create", response_model=ItemInventaryRead, status_code=status.HTTP_201_CREATED
)
async def create_item(
    item: ItemInventaryCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    category = await session.get(Category, item.category_id)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La categoria seleccionada no existe.",
        )
    new_item = ItemInventary.model_validate(item)
    session.add(new_item)
    await session.commit()
    await session.refresh(new_item)
    new_item.category = category
    return new_item


@router.get("/category/{category_id}", response_model=List[ItemInventaryRead])
async def get_items_by_category(
    category_id: int,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    query = (
        select(ItemInventary)
        .where(ItemInventary.category_id == category_id)
        .options(selectinload(ItemInventary.category))
    )
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/items", response_model=List[ItemInventaryRead])
async def get_items(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    query = select(ItemInventary).options(selectinload(ItemInventary.category))
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/dashboard/resumen")
async def get_dashboard_resumen(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    query = (
        select(
            Category.name,
            Category.color_hex,
            func.sum(ItemInventary.quantity).label("total"),
        )
        .join(ItemInventary, Category.id == ItemInventary.category_id)
        .group_by(Category.name, Category.color_hex)
    )
    result = await session.execute(query)
    global_total = result.all()

    resumen = []

    for row in global_total:
        resumen.append(
            {
                "category": row.count,
                "quantity_total": row.total or 0,
                "color_hex": row.color_hex,
            }
        )
    return resumen
