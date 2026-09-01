import uuid
from typing import Optional
from sqlmodel import SQLModel, Field


class BaseUser(SQLModel):
    username: str = Field(unique=True, index=True, nullable=False)
    email: str = Field(unique=True, index=True, nullable=False)
    level: int = Field(default=3, description="1: Admin, 2: Supervisor, 3: Usuario")
    department: Optional[str] = Field(default=None, description="Departamento o área del usuario")
    nombre_completo: Optional[str] = Field(default=None, description="Nombre completo del usuario")
    cargo: Optional[str] = Field(default=None, description="Cargo o puesto del usuario")
    hoja_vida: Optional[str] = Field(default=None, description="Hoja de vida / perfil del usuario")
    photo_data: Optional[str] = Field(default=None, nullable=True)
    photo_path: Optional[str] = Field(default=None, nullable=True)
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    is_verified: bool = Field(default=False)


class User(BaseUser, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str = Field(nullable=False)
