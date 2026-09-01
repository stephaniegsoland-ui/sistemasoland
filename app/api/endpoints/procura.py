import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.db import get_async_session
from app.core.notifications import broadcast_notification
from app.models.procura import Procura
from app.models.inventory import ItemInventary
from app.models.notification import Notification
from app.schemas.procura import ProcuraRead, ProcuraCreate, ProcuraUpdate
from app.core.auth import current_active_user
from app.models.user import User

AUTHORIZED_PROCURA_STATUSES = {"pending", "in_progress", "completed", "approved", "rejected"}

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
                    "item_id": str(found.id) if found.id is not None else None,
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

    new_procura = Procura.model_validate(procura.model_dump())
    new_procura.items = processed_items
    new_procura.usage = procura.usage
    new_procura.notes = procura.notes
    new_procura.total_cost = procura.total_cost
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
    q = select(Procura)
    if not hasattr(user, "level") or user.level > 2:
        q = q.where(Procura.requester_id == user.id)
    q = q.order_by(Procura.updated_at.desc())
    result = await session.execute(q)
    return result.scalars().all()


@router.get("/{procura_id}", response_model=ProcuraRead)
async def get_procura(
    procura_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    procura = await session.get(Procura, procura_id)
    if not procura:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Procura no encontrada")
    if user.level > 2 and procura.requester_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso denegado")
    return procura


@router.put("/{procura_id}", response_model=ProcuraRead)
async def update_procura(
    procura_id: uuid.UUID,
    procura_in: ProcuraUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    procura = await session.get(Procura, procura_id)
    if not procura:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Procura no encontrada")
    if user.level > 2 and procura.requester_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso denegado")

    update_data = procura_in.model_dump(exclude_unset=True)
    if "status" in update_data:
        if not hasattr(user, "level") or user.level > 2:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo administradores o supervisores pueden actualizar el estado.",
            )
        status_value = str(update_data["status"]).strip().lower()
        if status_value not in AUTHORIZED_PROCURA_STATUSES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Estado de procura no válido")
        update_data["status"] = status_value

    status_changed = False
    previous_status = procura.status

    for key, value in update_data.items():
        setattr(procura, key, value)
        if key == "status" and value != previous_status:
            status_changed = True

    procura.updated_at = datetime.utcnow()
    session.add(procura)

    notification = None
    if status_changed and procura.requester_id is not None:
        notification = Notification(
            user_id=procura.requester_id,
            title="Actualización de Procura",
            message=(
                f"El estado de tu procura "
                f"{procura.reference or str(procura.id)} "
                f"cambió a {procura.status}."
            ),
            type="status",
            read=False,
        )
        session.add(notification)

    await session.commit()
    await session.refresh(procura)
    if notification is not None:
        await session.refresh(notification)
        try:
            broadcast_notification(procura.requester_id, {
                "id": str(notification.id),
                "user_id": str(notification.user_id),
                "title": notification.title,
                "message": notification.message,
                "type": notification.type,
                "read": notification.read,
                "created_at": notification.created_at.isoformat(),
            })
        except Exception:
            pass

    return procura


@router.delete("/{procura_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_procura(
    procura_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    procura = await session.get(Procura, procura_id)
    if not procura:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Procura no encontrada")
    await session.delete(procura)
    await session.commit()
    return None
