import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, JSON, Text
from sqlmodel import Field, SQLModel


class SystemSettings(SQLModel, table=True):
    __tablename__ = "system_settings"

    key: str = Field(default="general", primary_key=True, max_length=64)
    company_name: str = Field(default="Soland", max_length=255)
    support_email: Optional[str] = Field(default=None, max_length=255)
    support_phone: Optional[str] = Field(default=None, max_length=64)
    company_address: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    default_timezone: str = Field(default="America/Caracas", max_length=64)
    expiry_reminder_days: List[int] = Field(default_factory=lambda: [30, 7, 1], sa_column=Column(JSON, nullable=False))
    ocr_language: str = Field(default="auto", max_length=8)
    max_document_upload_mb: int = Field(default=15, ge=1, le=50)
    updated_by: Optional[uuid.UUID] = Field(default=None, foreign_key="user.id", nullable=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    actor_id: Optional[uuid.UUID] = Field(default=None, foreign_key="user.id", index=True, nullable=True)
    actor_username: str = Field(max_length=50, nullable=False)
    action: str = Field(max_length=100, index=True, nullable=False)
    entity_type: str = Field(max_length=100, nullable=False)
    entity_id: Optional[str] = Field(default=None, max_length=64, nullable=True)
    details: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True, nullable=False)