import base64
import io
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile, status
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.auth import UserManager, current_active_user, get_supervisor_or_admin
from app.core.db import get_async_session, get_user_db
from app.models.user import User
from app.schemas.user import UserRead, UserUpdate

STATIC_USERS_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "users"
STATIC_USERS_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()

def decode_photo_data(photo_data: str) -> bytes:
    if photo_data.startswith("data:"):
        try:
            _, encoded = photo_data.split(",", 1)
        except ValueError:
            encoded = photo_data
    else:
        encoded = photo_data
    return base64.b64decode(encoded)


def save_user_photo_bytes(user_id: uuid.UUID, photo_bytes: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Foto inválida o corrupta.") from exc

    filename = f"{user_id}.png"
    target_path = STATIC_USERS_DIR / filename
    image.save(target_path, format="PNG")
    return f"/static/users/{filename}"


@router.get("/", response_model=List[UserRead], summary="List all users")
async def list_users(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_supervisor_or_admin),
):
    result = await session.execute(select(User))
    users = result.scalars().all()
    return users


async def _authorize_user_edit(user_id: uuid.UUID, current_user: User) -> User:
    if current_user.id == user_id:
        return current_user
    if not hasattr(current_user, "level") or current_user.level > 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado. Se requiere Administrador o Supervisor.",
        )
    return current_user


@router.get("/admin/{user_id}", response_model=UserRead, summary="Get a user by id for admin/supervisor or self")
async def get_user_by_id(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    _ = await _authorize_user_edit(user_id, current_user)
    result = await session.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")
    return target_user


@router.patch("/admin/{user_id}", response_model=UserRead, summary="Update a user by id for admin/supervisor or self")
async def update_user_admin(
    user_id: uuid.UUID,
    user_update: UserUpdate = Body(...),
    session: AsyncSession = Depends(get_async_session),
    user_db=Depends(get_user_db),
    current_user: User = Depends(current_active_user),
):
    _ = await _authorize_user_edit(user_id, current_user)
    result = await session.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    update_data = user_update.dict(exclude_unset=True)
    password = update_data.pop("password", None)
    if password:
        manager = UserManager(user_db)
        update_data["hashed_password"] = manager.password_helper.hash(password)

    for key, value in update_data.items():
        setattr(target_user, key, value)

    session.add(target_user)
    await session.commit()
    await session.refresh(target_user)
    return target_user


@router.patch("/{user_id}/photo", response_model=UserRead, summary="Upload or update a user photo")
async def update_user_photo(
    user_id: uuid.UUID,
    photo: Optional[UploadFile] = File(None),
    photo_data: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    if photo is None and photo_data is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Se requiere una foto para actualizar.")

    _ = await _authorize_user_edit(user_id, current_user)
    result = await session.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    if photo is not None:
        photo_bytes = await photo.read()
    else:
        try:
            photo_bytes = decode_photo_data(photo_data)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="photo_data inválida.") from exc

    photo_path = save_user_photo_bytes(user_id, photo_bytes)
    target_user.photo_path = photo_path
    target_user.photo_data = None
    session.add(target_user)
    await session.commit()
    await session.refresh(target_user)

    return target_user
