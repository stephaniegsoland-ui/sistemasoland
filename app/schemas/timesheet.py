import uuid
from datetime import date, datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class TimesheetEntryCreate(BaseModel):
    date: date
    start: Optional[str] = None  # HH:MM or ISO
    end: Optional[str] = None
    activity: Optional[str] = None
    viaticos: Optional[float] = 0.0
    viatico_type: Optional[str] = None


class TimesheetEntryRead(TimesheetEntryCreate):
    hours: float = 0.0


class TimesheetCreate(BaseModel):
    period_start: date
    period_end: date
    entries: List[TimesheetEntryCreate] = []
    viaticos: Optional[float] = 0.0
    reference: Optional[str] = None
    description: Optional[str] = None


class TimesheetRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: Optional[str]
    user_department: Optional[str]
    period_start: date
    period_end: date
    entries: List[TimesheetEntryRead] = []
    total_hours: float
    viaticos: float
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
