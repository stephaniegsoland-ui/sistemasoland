from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.db import get_async_session
from app.models.vehicle import TypeRecord
from app.schemas.vehicle import TypeRecordCreate, TypeRecordRead
from app.core.auth import current_active_user, get_supervisor_or_admin
from app.models.user import User

router = APIRouter()


@router.post("/", response_model=TypeRecordRead, status_code=status.HTTP_201_CREATED)
async def create_type_record(
    type_record_in: TypeRecordCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_supervisor_or_admin),
):
    query = select(TypeRecord).where(TypeRecord.name == type_record_in.name)
    result = await session.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe un tipo de registro con ese nombre.",
        )
    new_type_record = TypeRecord.model_validate(type_record_in)
    session.add(new_type_record)
    await session.commit()
    await session.refresh(new_type_record)
    return new_type_record


@router.get("/", response_model=List[TypeRecordRead])
async def list_type_records(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    result = await session.execute(select(TypeRecord))
    return result.scalars().all()
