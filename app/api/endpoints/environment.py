import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.db import get_async_session
from app.models.environment import EnvironmentalDrill, EnvironmentalDocument, EnvironmentalTalk
from app.models.user import User

STATIC_ENVIRONMENT_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "environment" / "documents"
STATIC_ENVIRONMENT_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()


class EnvironmentalTalkCreate(BaseModel):
    title: str
    date: Optional[str] = "Próxima"
    audience: Optional[str] = "Todo el personal"
    owner: str


class EnvironmentalTalkRead(EnvironmentalTalkCreate):
    id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class EnvironmentalDrillCreate(BaseModel):
    title: str
    date: Optional[str] = "Próxima"
    zone: str
    status: Optional[str] = "Programado"


class EnvironmentalDrillRead(EnvironmentalDrillCreate):
    id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class EnvironmentalDocumentCreate(BaseModel):
    title: str
    type: Optional[str] = "PDF"
    owner: Optional[str] = "Dpto. Ambiente"
    comment: Optional[str] = None
    file_name: Optional[str] = None


class EnvironmentalDocumentRead(EnvironmentalDocumentCreate):
    id: uuid.UUID
    file_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/talks", response_model=List[EnvironmentalTalkRead], status_code=status.HTTP_200_OK)
async def list_talks(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    result = await session.execute(select(EnvironmentalTalk).order_by(EnvironmentalTalk.created_at.desc()))
    return result.scalars().all()


@router.post("/talks", response_model=EnvironmentalTalkRead, status_code=status.HTTP_201_CREATED)
async def create_talk(
    payload: EnvironmentalTalkCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="El título es obligatorio.")
    talk = EnvironmentalTalk(
        title=payload.title.strip(),
        date=payload.date or "Próxima",
        audience=payload.audience or "Todo el personal",
        owner=payload.owner.strip(),
    )
    session.add(talk)
    await session.commit()
    await session.refresh(talk)
    return talk


@router.get("/drills", response_model=List[EnvironmentalDrillRead], status_code=status.HTTP_200_OK)
async def list_drills(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    result = await session.execute(select(EnvironmentalDrill).order_by(EnvironmentalDrill.created_at.desc()))
    return result.scalars().all()


@router.post("/drills", response_model=EnvironmentalDrillRead, status_code=status.HTTP_201_CREATED)
async def create_drill(
    payload: EnvironmentalDrillCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    if not payload.title.strip() or not payload.zone.strip():
        raise HTTPException(status_code=400, detail="Título y zona son obligatorios.")
    drill = EnvironmentalDrill(
        title=payload.title.strip(),
        date=payload.date or "Próxima",
        zone=payload.zone.strip(),
        status=payload.status or "Programado",
    )
    session.add(drill)
    await session.commit()
    await session.refresh(drill)
    return drill


@router.get("/documents", response_model=List[EnvironmentalDocumentRead], status_code=status.HTTP_200_OK)
async def list_documents(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    result = await session.execute(select(EnvironmentalDocument).order_by(EnvironmentalDocument.created_at.desc()))
    return result.scalars().all()


@router.post("/documents", response_model=EnvironmentalDocumentRead, status_code=status.HTTP_201_CREATED)
async def create_document(
    payload: EnvironmentalDocumentCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="El nombre del documento es obligatorio.")
    document = EnvironmentalDocument(
        title=payload.title.strip(),
        type=payload.type or "PDF",
        owner=payload.owner or "Dpto. Ambiente",
        comment=payload.comment,
        file_name=payload.file_name,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


@router.post("/documents/upload", response_model=EnvironmentalDocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    title: str = Form(...),
    doc_type: str = Form("PDF"),
    owner: str = Form("Dpto. Ambiente"),
    comment: Optional[str] = Form(None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    if not title.strip():
        raise HTTPException(status_code=400, detail="El nombre del documento es obligatorio.")

    if file.filename is None or not file.filename.strip():
        raise HTTPException(status_code=400, detail="Debe adjuntar un archivo.")

    suffix = Path(file.filename).suffix or ".pdf"
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    destination = STATIC_ENVIRONMENT_DIR / stored_name
    contents = await file.read()
    destination.write_bytes(contents)

    document = EnvironmentalDocument(
        title=title.strip(),
        type=doc_type or "PDF",
        owner=owner or "Dpto. Ambiente",
        comment=comment,
        file_name=f"/static/environment/documents/{stored_name}",
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document
