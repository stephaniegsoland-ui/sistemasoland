import uuid
from typing import Dict, Any, Optional
from pydantic import BaseModel


class CategoryRead(BaseModel):
    id: int
    name: str
    color_hex: str
    icon: str


class CategoryCreate(BaseModel):
    name: str
    color_hex: str
    icon: Optional[str] = "box"


class ItemInventaryRead(BaseModel):
    id: uuid.UUID
    name: str
    quantity: int
    attribute: Dict[str, Any]
    category_id: int
    category: Optional[CategoryRead] = None

    class Config:
        from_attributes = True


class ItemInventaryCreate(BaseModel):
    name: str
    quantity: int
    category_id: int
    attribute: Dict[str, Any] = {}
