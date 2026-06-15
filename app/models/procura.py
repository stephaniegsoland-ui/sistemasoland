import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from sqlalchemy import String
from sqlmodel import SQLModel, Field, Column, JSON


class Procura(SQLModel, table=True):
    __tablename__ = "procura"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    reference: Optional[str] = Field(default=None, index=True, unique=True)
    description: Optional[str] = None
    supplier: Optional[str] = None
    usage: Optional[str] = Field(default=None, sa_column=Column("use", String(255), nullable=True))
    requester_id: Optional[uuid.UUID] = Field(default=None, nullable=True, index=True)
    requester_name: Optional[str] = None
    requester_department: Optional[str] = None
    status: str = Field(default="pending")
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    items: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    attribute: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

