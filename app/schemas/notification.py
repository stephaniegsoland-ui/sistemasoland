import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class NotificationRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    message: str
    type: str
    read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationCreate(BaseModel):
    user_id: uuid.UUID
    title: str
    message: str
    type: Optional[str] = "info"


class NotificationUpdate(BaseModel):
    read: Optional[bool] = None
