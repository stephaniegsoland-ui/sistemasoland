import base64
import io
import secrets
import uuid
import pyotp
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile, status
from fastapi_users.password import PasswordHelper
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from PIL import Image
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.auth import UserManager, current_active_user, get_supervisor_or_admin
from app.core.audit import add_audit_entry
from app.core.mfa import decrypt_totp_secret, encrypt_totp_secret, verify_totp_secret
from app.core.db import get_async_session, get_user_db
from app.core.storage import media_dir, read_upload
from app.models.user import User
from app.schemas.user import UserRead, UserUpdate

STATIC_USERS_DIR = media_dir("users")

router = APIRouter()


class UserProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Optional[str] = Field(default=None, min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    nombre_completo: Optional[str] = Field(default=None, max_length=255)
    department: Optional[str] = Field(default=None, max_length=255)
    cargo: Optional[str] = Field(default=None, max_length=255)
    hoja_vida: Optional[str] = Field(default=None, max_length=2000)


class UserPasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class MFASetupRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)


class MFACodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class MFADisableRequest(MFACodeRequest):
    current_password: str = Field(min_length=1, max_length=128)


SELF_RESTRICTED_USER_FIELDS = {"password", "level", "permissions", "is_active", "is_superuser", "is_verified", "session_version", "mfa_enabled", "mfa_secret_encrypted", "mfa_pending_secret_encrypted", "mfa_recovery_codes", "last_login_at"}
ADMIN_ONLY_USER_FIELDS = {"level", "permissions", "is_superuser", "is_active", "is_verified", "session_version", "mfa_enabled", "mfa_secret_encrypted", "mfa_pending_secret_encrypted", "mfa_recovery_codes", "last_login_at"}

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


def _authorize_user_update_fields(user_id: uuid.UUID, current_user: User, fields: set[str]) -> None:
    if current_user.id == user_id:
        forbidden_fields = fields & SELF_RESTRICTED_USER_FIELDS
        if forbidden_fields:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No puedes cambiar permisos, rol, estado ni contraseña desde tu propio perfil.",
            )

    if getattr(current_user, "level", 99) != 1 and not getattr(current_user, "is_superuser", False):
        if fields & ADMIN_ONLY_USER_FIELDS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo un administrador puede cambiar roles o estados de cuenta.",
            )


@router.patch("/me/profile", response_model=UserRead)
async def update_my_profile(
    payload: UserProfileUpdate,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    target_user = await session.get(User, current_user.id)
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if isinstance(value, str):
            value = value.strip()
            if key in {"username", "email"} and not value:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{key} no puede quedar vacío.")
            update_data[key] = value or None

    for field_name in ("username", "email"):
        value = update_data.get(field_name)
        if value and value != getattr(target_user, field_name):
            result = await session.execute(select(User.id).where(getattr(User, field_name) == value, User.id != target_user.id))
            if result.scalar_one_or_none() is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Ese {field_name} ya está en uso.")

    for key, value in update_data.items():
        setattr(target_user, key, value)
    add_audit_entry(session, actor=current_user, action="profile.updated", entity_type="user", entity_id=str(target_user.id), details={"fields": list(update_data)})
    session.add(target_user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El usuario o correo ya está en uso.") from exc
    await session.refresh(target_user)
    return target_user


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_my_password(
    payload: UserPasswordChange,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    target_user = await session.get(User, current_user.id)
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La nueva contraseña debe ser diferente.")

    password_helper = PasswordHelper()
    try:
        password_valid, _ = password_helper.verify_and_update(payload.current_password, target_user.hashed_password)
    except Exception:
        password_valid = False
    if not password_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La contraseña actual no es correcta.")

    target_user.hashed_password = password_helper.hash(payload.new_password)
    target_user.session_version += 1
    add_audit_entry(session, actor=current_user, action="password.changed", entity_type="user", entity_id=str(target_user.id))
    session.add(target_user)
    await session.commit()
    return None


@router.post("/me/mfa/setup")
async def setup_my_mfa(
    payload: MFASetupRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    target_user = await session.get(User, current_user.id)
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")
    if target_user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="La verificación en dos pasos ya está activa.")
    try:
        password_valid, _ = PasswordHelper().verify_and_update(payload.current_password, target_user.hashed_password)
    except Exception:
        password_valid = False
    if not password_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La contraseña actual no es correcta.")

    secret = pyotp.random_base32()
    target_user.mfa_pending_secret_encrypted = encrypt_totp_secret(secret)
    session.add(target_user)
    await session.commit()
    provisioning_uri = pyotp.TOTP(secret).provisioning_uri(name=target_user.email, issuer_name="Soland")
    return {"secret": secret, "provisioning_uri": provisioning_uri}


@router.post("/me/mfa/enable")
async def enable_my_mfa(
    payload: MFACodeRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    target_user = await session.get(User, current_user.id)
    if target_user is None or not target_user.mfa_pending_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Primero inicia la configuración MFA.")
    try:
        secret = decrypt_totp_secret(target_user.mfa_pending_secret_encrypted)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo leer la configuración MFA.") from exc
    if not verify_totp_secret(secret, payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El código de verificación no es válido.")

    target_user.mfa_secret_encrypted = target_user.mfa_pending_secret_encrypted
    target_user.mfa_pending_secret_encrypted = None
    target_user.mfa_enabled = True
    recovery_codes = [secrets.token_hex(5).upper() for _ in range(8)]
    target_user.mfa_recovery_codes = [PasswordHelper().hash(code) for code in recovery_codes]
    target_user.session_version += 1
    session.add(target_user)
    add_audit_entry(session, actor=current_user, action="mfa.enabled", entity_type="user", entity_id=str(target_user.id))
    await session.commit()
    return {"mfa_enabled": True, "sessions_revoked": True, "recovery_codes": recovery_codes}


@router.post("/me/mfa/disable")
async def disable_my_mfa(
    payload: MFADisableRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    target_user = await session.get(User, current_user.id)
    if target_user is None or not target_user.mfa_enabled or not target_user.mfa_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="La verificación en dos pasos no está activa.")
    try:
        password_valid, _ = PasswordHelper().verify_and_update(payload.current_password, target_user.hashed_password)
    except Exception:
        password_valid = False
    if not password_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La contraseña actual no es correcta.")
    secret = decrypt_totp_secret(target_user.mfa_secret_encrypted)
    if not verify_totp_secret(secret, payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El código de verificación no es válido.")

    target_user.mfa_enabled = False
    target_user.mfa_secret_encrypted = None
    target_user.mfa_pending_secret_encrypted = None
    target_user.mfa_recovery_codes = None
    target_user.session_version += 1
    session.add(target_user)
    add_audit_entry(session, actor=current_user, action="mfa.disabled", entity_type="user", entity_id=str(target_user.id))
    await session.commit()
    return {"mfa_enabled": False, "sessions_revoked": True}


@router.post("/me/sessions/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_my_sessions(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    target_user = await session.get(User, current_user.id)
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")
    target_user.session_version += 1
    session.add(target_user)
    add_audit_entry(session, actor=current_user, action="sessions.revoked", entity_type="user", entity_id=str(target_user.id))
    await session.commit()
    return None


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
    _authorize_user_update_fields(user_id, current_user, set(update_data))
    password = update_data.pop("password", None)
    changed_fields = list(update_data)
    if password:
        manager = UserManager(user_db)
        update_data["hashed_password"] = manager.password_helper.hash(password)

    for key, value in update_data.items():
        setattr(target_user, key, value)

    add_audit_entry(
        session,
        actor=current_user,
        action="user.updated",
        entity_type="user",
        entity_id=str(target_user.id),
        details={"fields": changed_fields, "password_changed": bool(password)},
    )
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
        photo_bytes = await read_upload(
            photo,
            allowed_extensions={".png", ".jpg", ".jpeg", ".webp"},
            allowed_content_types={"image/png", "image/jpeg", "image/webp"},
        )
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
