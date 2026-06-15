import uuid
from datetime import datetime, date
from typing import Optional, Dict, Any, List
from sqlmodel import SQLModel, Field, Column, JSON


class Timesheet(SQLModel, table=True):
    __tablename__ = "timesheet"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(nullable=False, index=True)
    user_name: Optional[str] = None
    user_department: Optional[str] = None
    period_start: date = Field(nullable=False)
    period_end: date = Field(nullable=False)
    entries: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    total_hours: float = Field(default=0.0)
    viaticos: float = Field(default=0.0)
    status: str = Field(default="pending")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
