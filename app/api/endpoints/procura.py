import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.db import get_async_session
from app.models.procura import Procura
from app.models.inventory import ItemInventary
from app.schemas.procura import ProcuraRead, ProcuraCreate, ProcuraUpdate
from app.core.auth import current_active_user
from app.models.user import User

router = APIRouter()


@router.post(
    "/create", response_model=ProcuraRead, status_code=status.HTTP_201_CREATED
)
async def create_procura(
    procura: ProcuraCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    processed_items = []

    for item in procura.items:
        item_id = getattr(item, "item_id", None)
        requested_qty = int(getattr(item, "quantity", 1))
        found = None

        if item_id:
            try:
                item_uuid = uuid.UUID(item_id)
                found = await session.get(ItemInventary, item_uuid)
            except Exception:
                found = None
        elif item.name:
            q = select(ItemInventary).where(ItemInventary.name == item.name)
            res = await session.execute(q)
            found = res.scalars().first()

        if found:
            available = int(found.quantity or 0)
            status_item = "available" if available >= requested_qty else "insufficient"
            processed_items.append(
                {
                    "item_id": found.id,
                    "name": found.name,
                    "quantity": requested_qty,
                    "available_quantity": available,
                    "status": status_item,
                }
            )
        else:
            # producto nuevo
            processed_items.append(
                {
                    "item_id": None,
                    "name": item.name,
                    "quantity": requested_qty,
                    "available_quantity": 0,
                    "status": "new_product",
                }
            )

    new_procura = Procura.model_validate(procura)
    new_procura.items = processed_items
    new_procura.use = procura.use
    new_procura.requester_id = user.id
    new_procura.requester_name = user.username
    new_procura.requester_department = getattr(user, "department", None)
    new_procura.status = "pending"
    new_procura.updated_at = datetime.utcnow()

    session.add(new_procura)
    await session.commit()
    await session.refresh(new_procura)
    return new_procura


@router.get("/", response_model=List[ProcuraRead])
async def list_procuras(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    result = await session.execute(select(Procura))
    return result.scalars().all()


@router.get("/{procura_id}", response_model=ProcuraRead)
async def get_procura(
    procura_id: str,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    procura = await session.get(Procura, procura_id)
    if not procura:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Procura no encontrada")
    return procura


@router.put("/{procura_id}", response_model=ProcuraRead)
async def update_procura(
    procura_id: str,
    procura_in: ProcuraUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    procura = await session.get(Procura, procura_id)
    if not procura:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Procura no encontrada")
    update_data = procura_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(procura, key, value)
    session.add(procura)
    await session.commit()
    await session.refresh(procura)
    return procura


@router.delete("/{procura_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_procura(
    procura_id: str,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    procura = await session.get(Procura, procura_id)
    if not procura:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Procura no encontrada")
    await session.delete(procura)
    await session.commit()
    return None
