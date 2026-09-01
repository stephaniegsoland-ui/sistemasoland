import uuid
from typing import Optional, Dict, Any, List
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


class VehicleInspectionRead(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID
    user_id: uuid.UUID
    before_image: str
    after_image: str
    before_images: Optional[List[str]] = None
    after_images: Optional[List[str]] = None
    diff_image: Optional[str] = None
    report: str
    score: float
    change_percent: float
    fuel_level: Optional[str] = None
    tire_condition: Optional[str] = None
    summary_tags: Optional[List[str]] = None
    notes: Optional[str] = None
    analysis_engine: Optional[str] = None
    analysis_mode: Optional[str] = None
    issues: Optional[List[Dict[str, Any]]] = None
    recommendations: Optional[List[str]] = None
    pdf_file: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class FleetRecordCreate(BaseModel):
    type_id: int
    km: int
    details: Dict[str, Any] = {}
