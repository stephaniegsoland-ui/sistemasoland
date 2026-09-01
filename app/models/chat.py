import uuid
from datetime import datetime
from sqlmodel import SQLModel, Field


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_message"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    content: str = Field(nullable=False, max_length=2000)
    sender_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, index=True)
    recipient_id: uuid.UUID | None = Field(default=None, foreign_key="user.id", nullable=True, index=True)
    message_type: str = Field(default="info", nullable=False, index=True)
    reference_title: str | None = Field(default=None, nullable=True)
    reference_url: str | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False, index=True)
