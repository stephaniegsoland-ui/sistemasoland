import uuid
from fastapi_users import schemas
from pydantic import EmailStr
from typing import Optional


class UserRead(schemas.BaseUser[uuid.UUID]):
    username: str
    email: EmailStr
    level: int
    is_active: int


class UserCreate(schemas.BaseUserCreate):
    username: str
    level: int = 3
    is_active: bool = True


class UserUpdate(schemas.BaseUserUpdate):
    username: Optional[str] = None
    level: Optional[int] = None
    is_active: Optional[bool] = None
