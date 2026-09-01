import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class ChatMessageRead(BaseModel):
    id: uuid.UUID
    content: str
    sender_id: uuid.UUID
    created_at: datetime
    sender_username: str | None = None
