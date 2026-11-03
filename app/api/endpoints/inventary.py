from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
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
    # Check if an item with the same name and category already exists.
    query = select(ItemInventary).where(
        ItemInventary.name == item.name,
        ItemInventary.category_id == item.category_id,
    )
    result = await session.execute(query)
    existing = result.scalars().first()

    if existing:
        # If exists, sum quantities and merge attributes (new values override)
        existing.quantity = (existing.quantity or 0) + (item.quantity or 0)
        if item.attribute:
            existing.attribute = {**existing.attribute, **item.attribute}
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        existing.category = category
        return existing

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
            ItemInventary.id,
            ItemInventary.name,
            ItemInventary.quantity,
            Category.name,
            Category.color_hex,
        )
        .join(ItemInventary, Category.id == ItemInventary.category_id)
        .order_by(ItemInventary.name.asc(), ItemInventary.id.asc())
    )
    result = await session.execute(query)
    rows = result.all()

    items = []
    # Return one chart entry per inventory item. Category totals remain available
    # through the categories endpoint used by the second dashboard chart.
    for row in rows:
        item_id, name, quantity, category_name, color = row
        items.append({
            "id": item_id,
            "name": name,
            "cantidad": int(quantity or 0),
            "category": category_name,
            "fill": color or "#3b82f6",
        })

    return {"items": items}
