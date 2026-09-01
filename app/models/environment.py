import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class EnvironmentalTalk(SQLModel, table=True):
    __tablename__ = "environmental_talks"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(nullable=False)
    date: str = Field(default="Próxima", nullable=False)
    audience: str = Field(default="Todo el personal", nullable=False)
    owner: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EnvironmentalDrill(SQLModel, table=True):
    __tablename__ = "environmental_drills"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(nullable=False)
    date: str = Field(default="Próxima", nullable=False)
    zone: str = Field(nullable=False)
    status: str = Field(default="Programado", nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EnvironmentalDocument(SQLModel, table=True):
    __tablename__ = "environmental_documents"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(nullable=False)
    type: str = Field(default="PDF", nullable=False)
    owner: str = Field(default="Dpto. Ambiente", nullable=False)
    comment: Optional[str] = Field(default=None, nullable=True)
    file_name: Optional[str] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
