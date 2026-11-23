import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Column, Date, DateTime, String, Text
from sqlmodel import Field, SQLModel


class QualityRecord(SQLModel, table=True):
    __tablename__ = "quality_records"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    record_type: str = Field(sa_column=Column(String(40), nullable=False))
    title: str = Field(sa_column=Column(String(200), nullable=False))
    description: str = Field(default="", sa_column=Column(Text, nullable=False))
    area: Optional[str] = Field(default=None, sa_column=Column(String(120), nullable=True))
    severity: str = Field(default="Media", sa_column=Column(String(20), nullable=False))
    status: str = Field(default="Abierta", sa_column=Column(String(20), nullable=False))
    responsible: Optional[str] = Field(default=None, sa_column=Column(String(120), nullable=True))
    due_date: Optional[date] = Field(default=None, sa_column=Column(Date, nullable=True))
    created_by: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False, default=datetime.utcnow),
    )


class CADDrawing(SQLModel, table=True):
    __tablename__ = "cad_drawings"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(sa_column=Column(String(200), nullable=False))
    drawing_code: Optional[str] = Field(default=None, sa_column=Column(String(100), nullable=True))
    revision: Optional[str] = Field(default=None, sa_column=Column(String(40), nullable=True))
    category: str = Field(default="Planos", sa_column=Column(String(80), nullable=False))
    notes: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    original_filename: str = Field(sa_column=Column(String(255), nullable=False))
    stored_filename: str = Field(sa_column=Column(String(80), nullable=False, unique=True))
    file_size: int = Field(nullable=False)
    uploaded_by: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False, default=datetime.utcnow),
    )
