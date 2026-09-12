import uuid
from fastapi_users import schemas
from pydantic import EmailStr
from typing import Any, Dict, List, Optional


class UserRead(schemas.BaseUser[uuid.UUID]):
    username: str
    email: EmailStr
    level: int
    department: Optional[str] = None
    nombre_completo: Optional[str] = None
    cargo: Optional[str] = None
    hoja_vida: Optional[str] = None
    photo_data: Optional[str] = None
    photo_path: Optional[str] = None
    permissions: Optional[List[str]] = None
    avatar_config: Optional[Dict[str, Any]] = None
    is_active: bool
    is_superuser: bool
    is_verified: bool


class UserCreate(schemas.BaseUserCreate):
    username: str
    department: Optional[str] = None
    nombre_completo: Optional[str] = None
    cargo: Optional[str] = None
    hoja_vida: Optional[str] = None
    photo_data: Optional[str] = None
    photo_path: Optional[str] = None
    permissions: Optional[List[str]] = None
    avatar_config: Optional[Dict[str, Any]] = None
    level: int = 3
    is_active: bool = True
    is_superuser: Optional[bool] = None
    is_verified: Optional[bool] = None


class UserUpdate(schemas.BaseUserUpdate):
    username: Optional[str] = None
    department: Optional[str] = None
    nombre_completo: Optional[str] = None
    cargo: Optional[str] = None
    hoja_vida: Optional[str] = None
    photo_data: Optional[str] = None
    photo_path: Optional[str] = None
    permissions: Optional[List[str]] = None
    avatar_config: Optional[Dict[str, Any]] = None
    level: Optional[int] = None
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None
    is_verified: Optional[bool] = None
