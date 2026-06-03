import uuid
from typing import Optional, Dict, Any
from pydantic import BaseModel
from datetime import date, datetime


class VehicleRead(BaseModel):
    id: uuid.UUID
    license_plate: str
    model: str
    km_actual: int
    status: str
    register_date: date
    user_id: Optional[uuid.UUID] = None

    class Config:
        from_attributes = True


class VehicleCreate(BaseModel):
    license_plate: str
    model: str
    km_actual: int = 0
    status: str = "Activo"
    register_date: date = date.today()
    user_id: Optional[uuid.UUID] = None


class TypeRecordRead(BaseModel):
    id: int
    name: str
    color_hex: str
    icon: str

    class Config:
        from_attributes = True


class TypeRecordCreate(BaseModel):
    name: str
    color_hex: Optional[str] = "#ffffff"
    icon: Optional[str] = "tool"


class FleetRecordRead(BaseModel):
    id: uuid.UUID
    date: datetime
    km: int
    vehicle_id: uuid.UUID
    user_id: uuid.UUID
    type_id: int
    type_record: Optional[TypeRecordRead] = None
    details: Dict[str, Any]

    class Config:
        from_attributes = True


class FleetRecordCreate(BaseModel):
    type_id: int
    km: int
    details: Dict[str, Any] = {}
