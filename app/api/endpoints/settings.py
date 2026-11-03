from datetime import datetime
from typing import List, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.audit import add_audit_entry
from app.core.auth import current_active_user
from app.core.db import get_async_session
from app.models.settings import AuditLog, SystemSettings
from app.models.user import User

router = APIRouter()


class UserPreferences(BaseModel):
    notifications_enabled: bool = True
    browser_notifications: bool = False
    document_expiry_days: Optional[List[int]] = Field(default=None, min_length=1, max_length=8)
    timezone: str = "America/Caracas"
    language: Literal["es-VE", "es-PE"] = "es-VE"
    density: Literal["comfortable", "compact"] = "comfortable"
    text_size: Literal["normal", "large"] = "normal"
    reduce_motion: bool = False

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Zona horaria no válida.") from exc
        return value

    @field_validator("document_expiry_days")
    @classmethod
    def validate_expiry_days(cls, values: Optional[List[int]]) -> Optional[List[int]]:
        if values is None:
            return None
        if any(value < 1 or value > 180 for value in values):
            raise ValueError("Los recordatorios deben estar entre 1 y 180 días.")
        if len(set(values)) != len(values):
            raise ValueError("No se permiten días de recordatorio duplicados.")
        return sorted(values, reverse=True)


class SystemSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=255)
    support_email: Optional[EmailStr] = None
    support_phone: Optional[str] = Field(default=None, max_length=64)
    company_address: Optional[str] = Field(default=None, max_length=2000)
    default_timezone: str = "America/Caracas"
    expiry_reminder_days: List[int] = Field(default_factory=lambda: [30, 7, 1], min_length=1, max_length=8)
    ocr_language: Literal["auto", "spa", "eng"] = "auto"
    max_document_upload_mb: int = Field(default=15, ge=1, le=50)

    @field_validator("default_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Zona horaria no válida.") from exc
        return value

    @field_validator("expiry_reminder_days")
    @classmethod
    def validate_expiry_days(cls, values: List[int]) -> List[int]:
        if any(value < 1 or value > 180 for value in values) or len(set(values)) != len(values):
            raise ValueError("Indica hasta ocho días únicos entre 1 y 180.")
        return sorted(values, reverse=True)


class SystemSettingsRead(SystemSettingsUpdate):
    updated_at: datetime


class AuditLogRead(BaseModel):
    id: str
    actor_id: Optional[str]
    actor_username: str
    action: str
    entity_type: str
    entity_id: Optional[str]
    details: dict
    created_at: datetime


def _require_admin(user: User) -> None:
    if user.level != 1 and not user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo un administrador puede acceder a esta configuración.")


def _system_settings_read(settings: SystemSettings) -> SystemSettingsRead:
    return SystemSettingsRead(
        company_name=settings.company_name,
        support_email=settings.support_email,
        support_phone=settings.support_phone,
        company_address=settings.company_address,
        default_timezone=settings.default_timezone,
        expiry_reminder_days=settings.expiry_reminder_days,
        ocr_language=settings.ocr_language,
        max_document_upload_mb=settings.max_document_upload_mb,
        updated_at=settings.updated_at,
    )


@router.get("/preferences", response_model=UserPreferences)
async def get_preferences(user: User = Depends(current_active_user)):
    return UserPreferences.model_validate((user.settings or {}).get("preferences", {}))


@router.put("/preferences", response_model=UserPreferences)
async def save_preferences(
    preferences: UserPreferences,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    current = await session.get(User, user.id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")
    current.settings = {**(current.settings or {}), "preferences": preferences.model_dump()}
    add_audit_entry(session, actor=current, action="preferences.updated", entity_type="user", entity_id=str(current.id), details={"fields": list(preferences.model_fields_set)})
    session.add(current)
    await session.commit()
    return preferences


@router.get("/system", response_model=SystemSettingsRead)
async def get_system_settings(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    settings = await session.get(SystemSettings, "general") or SystemSettings()
    return _system_settings_read(settings)


@router.put("/system", response_model=SystemSettingsRead)
async def save_system_settings(
    payload: SystemSettingsUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    _require_admin(user)
    settings = await session.get(SystemSettings, "general")
    if settings is None:
        settings = SystemSettings(key="general")
    values = payload.model_dump()
    for field, value in values.items():
        setattr(settings, field, value.strip() or None if isinstance(value, str) else value)
    settings.updated_by = user.id
    settings.updated_at = datetime.utcnow()
    session.add(settings)
    add_audit_entry(session, actor=user, action="system.settings.updated", entity_type="system_settings", entity_id="general", details={"fields": list(values)})
    await session.commit()
    await session.refresh(settings)
    return _system_settings_read(settings)


@router.get("/audit", response_model=List[AuditLogRead])
async def list_audit_log(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    _require_admin(user)
    result = await session.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))
    return [AuditLogRead(
        id=str(item.id), actor_id=str(item.actor_id) if item.actor_id else None,
        actor_username=item.actor_username, action=item.action, entity_type=item.entity_type,
        entity_id=item.entity_id, details=item.details, created_at=item.created_at,
    ) for item in result.scalars().all()]