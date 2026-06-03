import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.db import get_async_session
from app.models.vehicle import Vehicle, FleetRecord, TypeRecord
from app.schemas.vehicle import (
    VehicleCreate,
    VehicleRead,
    FleetRecordCreate,
    FleetRecordRead,
)
from app.core.auth import current_active_user, get_supervisor_or_admin
from app.models.user import User

router = APIRouter()


@router.get("/", response_model=List[VehicleRead])
async def list_vehicles(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    result = await session.execute(select(Vehicle))
    return result.scalars().all()


@router.get("/{vehicle_id}/record", response_model=List[FleetRecordRead])
async def list_fleet_records(
    vehicle_id: int,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    query = (
        select(FleetRecord)
        .where(FleetRecord.vehicle_id == vehicle_id)
        .options(selectinload(FleetRecord.type_record))
        .order_by(FleetRecord.date.desc())
    )

    result = await session.execute(query)
    return result.scalars().all()


@router.post("/", response_model=VehicleRead, status_code=status.HTTP_201_CREATED)
async def create_vehicle(
    vehicle_in: VehicleCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_supervisor_or_admin),
):
    new_vehicle = Vehicle.model_validate(vehicle_in)
    session.add(new_vehicle)
    await session.commit()
    await session.refresh(new_vehicle)
    return new_vehicle


@router.post(
    "/{vehicle_id}/record",
    response_model=FleetRecordRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_fleet_record(
    vehicle_id: uuid.UUID,
    record_in: FleetRecordCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vehiculo no encontrado."
        )

    if vehicle.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado. Solo el usuario que tiene asignado este vehiculo puede reportar.",
        )

    type_record = await session.get(TypeRecord, record_in.type_id)
    if not type_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tipo de registro no encontrado.",
        )

    new_record = FleetRecord(
        vehicle_id=vehicle_id,
        user_id=user.id,
        type_id=record_in.type_id,
        km=record_in.km,
        details=record_in.details,
    )

    new_record.type_record = type_record

    if record_in.km > vehicle.km_actual:
        vehicle.km_actual = record_in.km
        session.add(vehicle)

    session.add(new_record)
    await session.commit()
    await session.refresh(new_record)
    return new_record
