import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel


class ProcuraItemRead(BaseModel):
    item_id: Optional[uuid.UUID]
    name: Optional[str]
    quantity: int
    status: Optional[str]
    available_quantity: Optional[int]


class ProcuraItemCreate(BaseModel):
    item_id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    quantity: int = 1


class ProcuraRead(BaseModel):
    id: uuid.UUID
    reference: Optional[str]
    description: Optional[str]
    supplier: Optional[str]
    usage: Optional[str]
    requester_id: Optional[uuid.UUID]
    requester_name: Optional[str]
    requester_department: Optional[str]
    status: str
    requested_at: datetime
    updated_at: datetime
    items: List[ProcuraItemRead] = []
    attribute: Dict[str, Any]

    class Config:
        from_attributes = True


class ProcuraCreate(BaseModel):
    reference: Optional[str] = None
    description: Optional[str] = None
    supplier: Optional[str] = None
    usage: Optional[str] = None
    items: List[ProcuraItemCreate] = []
    attribute: Dict[str, Any] = {}


class ProcuraUpdate(BaseModel):
    reference: Optional[str] = None
    description: Optional[str] = None
    supplier: Optional[str] = None
    usage: Optional[str] = None
    status: Optional[str] = None
    items: Optional[List[ProcuraItemCreate]] = None
    attribute: Optional[Dict[str, Any]] = None
