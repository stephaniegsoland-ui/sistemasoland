import uuid
from typing import Optional
from sqlmodel import SQLModel, Field


class BaseUser(SQLModel):
    username: str = Field(unique=True, index=True, nullable=False)
    email: str = Field(unique=True, index=True, nullable=False)
    level: int = Field(default=3, description="1: Admin, 2: Supervisor, 3: Usuario")
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    is_verified: bool = Field(default=False)


class User(BaseUser, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str = Field(nullable=False)
