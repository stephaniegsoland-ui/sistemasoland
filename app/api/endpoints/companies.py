import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.auth import current_active_user
from app.core.db import get_async_session
from app.models.company import Company, CompanyRetention
from app.models.user import User
from app.schemas.company import (
    CompanyCreate,
    CompanyRead,
    CompanyRetentionCreate,
    CompanyRetentionRead,
    CompanyRetentionUpdate,
    CompanyUpdate,
    CompanyWithRetentions,
)

router = APIRouter()


@router.get("/", response_model=List[CompanyRead], status_code=status.HTTP_200_OK)
async def list_companies(
    query: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    stmt = select(Company).order_by(Company.updated_at.desc())
    if query:
        like = f"%{query.lower()}%"
        stmt = stmt.where(
            (Company.name.ilike(f"%{query}%")) | (Company.rif.ilike(f"%{query}%"))
        )
    result = await session.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
async def create_company(
    company_in: CompanyCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    normalized_name = (company_in.name or "").strip()
    normalized_rif = (company_in.rif or "").strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="El nombre de la empresa es obligatorio.")
    if not normalized_rif:
        raise HTTPException(status_code=400, detail="El RIF de la empresa es obligatorio.")

    existing = await session.execute(
        select(Company).where((Company.rif == normalized_rif) | (Company.name == normalized_name))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ya existe una empresa con ese nombre o RIF.")

    company = Company(
        name=normalized_name,
        rif=normalized_rif,
        address=company_in.address,
        phone=company_in.phone,
        email=company_in.email,
        contact_name=company_in.contact_name,
        status=company_in.status or "activo",
        default_retention_percent=float(company_in.default_retention_percent or 0),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(company)
    await session.commit()
    await session.refresh(company)
    return company


@router.get("/{company_id}", response_model=CompanyWithRetentions)
async def get_company(
    company_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")

    retentions = await session.execute(
        select(CompanyRetention).where(CompanyRetention.company_id == company_id).order_by(CompanyRetention.created_at.desc())
    )
    records = retentions.scalars().all()
    return {
        **company.model_dump(),
        "retentions": [r.model_dump() for r in records],
        "retention_count": len(records),
    }


@router.put("/{company_id}", response_model=CompanyRead)
async def update_company(
    company_id: uuid.UUID,
    company_in: CompanyUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")

    update_data = company_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if value is not None:
            setattr(company, field, value)
    company.updated_at = datetime.utcnow()
    session.add(company)
    await session.commit()
    await session.refresh(company)
    return company


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    await session.delete(company)
    await session.commit()
    return None


@router.get("/{company_id}/retentions", response_model=List[CompanyRetentionRead])
async def list_company_retentions(
    company_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")

    result = await session.execute(
        select(CompanyRetention).where(CompanyRetention.company_id == company_id).order_by(CompanyRetention.created_at.desc())
    )
    return result.scalars().all()


@router.post("/{company_id}/retentions", response_model=CompanyRetentionRead, status_code=status.HTTP_201_CREATED)
async def create_company_retention(
    company_id: uuid.UUID,
    retention_in: CompanyRetentionCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")

    retention = CompanyRetention(
        company_id=company_id,
        description=retention_in.description,
        amount=float(retention_in.amount or 0),
        percent=float(retention_in.percent or 0),
        status=retention_in.status or "pendiente",
        due_date=retention_in.due_date,
    )
    session.add(retention)
    company.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(retention)
    return retention


@router.patch("/{company_id}/retentions/{retention_id}", response_model=CompanyRetentionRead)
async def update_company_retention(
    company_id: uuid.UUID,
    retention_id: uuid.UUID,
    retention_in: CompanyRetentionUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    retention = await session.get(CompanyRetention, retention_id)
    if not retention or retention.company_id != company_id:
        raise HTTPException(status_code=404, detail="Retención no encontrada.")

    update_data = retention_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if value is not None:
            setattr(retention, field, value)
    await session.commit()
    await session.refresh(retention)
    return retention


@router.delete("/{company_id}/retentions/{retention_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company_retention(
    company_id: uuid.UUID,
    retention_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    retention = await session.get(CompanyRetention, retention_id)
    if not retention or retention.company_id != company_id:
        raise HTTPException(status_code=404, detail="Retención no encontrada.")
    await session.delete(retention)
    await session.commit()
    return None
