import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    message_type: str = Field(default="info")
    reference_title: str | None = None
    reference_url: str | None = None
    recipient_id: uuid.UUID | None = None


class ChatMessageRead(BaseModel):
    id: uuid.UUID
    content: str
    sender_id: uuid.UUID
    recipient_id: uuid.UUID | None = None
    created_at: datetime
    sender_username: str | None = None
    message_type: str = "info"
    reference_title: str | None = None
    reference_url: str | None = None
