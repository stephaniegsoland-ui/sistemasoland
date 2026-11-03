from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.db import get_async_session
from app.models.user import User
from app.schemas.user import UserRead
from app.core.auth import get_supervisor_or_admin

router = APIRouter()


@router.get("/", response_model=List[UserRead], tags=["Gestion de Usuarios"])
async def list_all_users(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_supervisor_or_admin),
):
    query = select(User)
    result = await session.execute(query)
    users = result.scalars().all()

    return users
