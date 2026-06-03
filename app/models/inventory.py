import uuid
from typing import Dict, Any, List, Optional
from sqlmodel import SQLModel, Field, Column, JSON, Relationship


class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    description: Optional[str] = None

    color_hex: Optional[str] = Field(default="#ffffff")
    icon: Optional[str] = Field(default="box")
    items: List["ItemInventary"] = Relationship(back_populates="category")


class ItemInventary(SQLModel, table=True):
    __tablename__ = "inventary"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(index=True)
    quantity: int = Field(default=0)
    attribute: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    category_id: int = Field(foreign_key="category.id")
    category: Optional[Category] = Relationship(back_populates="items")
