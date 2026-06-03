import uuid
from typing import Optional, Dict, Any, List
from sqlmodel import SQLModel, Field, Column, JSON, Relationship
from datetime import date, datetime


class VehicleBase(SQLModel):
    license_plate: str = Field(unique=True, index=True, nullable=False)
    model: str = Field(nullable=False)
    km_actual: int = Field(default=0)
    status: str = Field(default="Activo")
    register_date: date = Field(default_factory=date.today)


class Vehicle(VehicleBase, table=True):
    __tablename__ = "vehicles"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="user.id")
    records: List["FleetRecord"] = Relationship(back_populates="vehicle")


class TypeRecord(SQLModel, table=True):
    __tablename__ = "type_record"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    color_hex: Optional[str] = Field(default="#ffffff")
    icon: Optional[str] = Field(default="tool")

    record: List["FleetRecord"] = Relationship(back_populates="type_record")


class FleetRegistryBase(SQLModel):
    date: datetime = Field(default_factory=datetime.now)
    km: int = Field(description="Kilometraje del vehículo al momento del reporte")


class FleetRecord(SQLModel, table=True):
    __tablename__ = "fleet_record"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    vehicle_id: uuid.UUID = Field(foreign_key="vehicles.id", nullable=False)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)

    type_id: int = Field(foreign_key="type_record.id", nullable=False)
    details: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    vehicle: Optional["Vehicle"] = Relationship(back_populates="records")
    type_record: Optional["TypeRecord"] = Relationship(back_populates="record")
