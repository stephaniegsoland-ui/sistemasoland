import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, String
from sqlmodel import SQLModel, Field


class Notification(SQLModel, table=True):
    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        sa_column=Column("id", String(32), primary_key=True),
    )
    user_id: str = Field(
        sa_column=Column("user_id", String(36), nullable=False),
    )
    title: str = Field(
        sa_column=Column("title", String(255), nullable=False),
    )
    message: str = Field(
        sa_column=Column("message", String(255), nullable=False),
    )
    type: str = Field(
        default="info",
        sa_column=Column("type", String(255), nullable=False),
    )
    payload: str | None = Field(
        default=None,
        sa_column=Column("payload", String(255), nullable=True),
    )
    is_read: bool = Field(
        default=False,
        sa_column=Column("is_read", Boolean, nullable=False, default=False),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    )

    @property
    def read(self) -> bool:
        return bool(self.is_read)

    @read.setter
    def read(self, value: bool) -> None:
        self.is_read = bool(value)
