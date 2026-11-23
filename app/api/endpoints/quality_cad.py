import logging
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field as PydanticField, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.auth import current_active_user
from app.core.db import get_async_session
from app.core.storage import media_dir, read_upload, safe_filename_suffix
from app.models.quality_cad import CADDrawing, QualityRecord
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

CAD_FILES_DIR = media_dir("cad")
CAD_ALLOWED_EXTENSIONS = {
    ".pdf", ".dwg", ".dxf", ".dwt", ".dgn", ".step", ".stp", ".iges", ".igs",
    ".stl", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".doc", ".docx", ".xls",
    ".xlsx", ".csv", ".txt",
}
CAD_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

QualityType = Literal["No conformidad", "Acción correctiva", "Auditoría", "Inspección"]
QualitySeverity = Literal["Baja", "Media", "Alta", "Crítica"]
QualityStatus = Literal["Abierta", "En proceso", "Cerrada"]


class QualityRecordCreate(BaseModel):
    record_type: QualityType
    title: str = PydanticField(min_length=1, max_length=200)
    description: str = PydanticField(default="", max_length=5000)
    area: Optional[str] = PydanticField(default=None, max_length=120)
    severity: QualitySeverity = "Media"
    status: QualityStatus = "Abierta"
    responsible: Optional[str] = PydanticField(default=None, max_length=120)
    due_date: Optional[date] = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("El título es obligatorio.")
        return normalized


class QualityRecordUpdate(BaseModel):
    record_type: Optional[QualityType] = None
    title: Optional[str] = PydanticField(default=None, min_length=1, max_length=200)
    description: Optional[str] = PydanticField(default=None, max_length=5000)
    area: Optional[str] = PydanticField(default=None, max_length=120)
    severity: Optional[QualitySeverity] = None
    status: Optional[QualityStatus] = None
    responsible: Optional[str] = PydanticField(default=None, max_length=120)
    due_date: Optional[date] = None


class QualityRecordRead(BaseModel):
    id: uuid.UUID
    record_type: str
    title: str
    description: str
    area: Optional[str]
    severity: str
    status: str
    responsible: Optional[str]
    due_date: Optional[date]
    created_by: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class CADDrawingRead(BaseModel):
    id: uuid.UUID
    title: str
    drawing_code: Optional[str]
    revision: Optional[str]
    category: str
    notes: Optional[str]
    original_filename: str
    file_size: int
    uploaded_by: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/quality/records", response_model=list[QualityRecordRead])
async def list_quality_records(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    result = await session.execute(
        select(QualityRecord).order_by(QualityRecord.created_at.desc())
    )
    return result.scalars().all()


@router.post(
    "/quality/records",
    response_model=QualityRecordRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_quality_record(
    payload: QualityRecordCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    record = QualityRecord(**payload.model_dump(), created_by=user.id)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


@router.patch("/quality/records/{record_id}", response_model=QualityRecordRead)
async def update_quality_record(
    record_id: uuid.UUID,
    payload: QualityRecordUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    record = await session.get(QualityRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Registro de calidad no encontrado.")

    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "title" and value is not None:
            value = value.strip()
            if not value:
                raise HTTPException(status_code=400, detail="El título es obligatorio.")
        setattr(record, key, value)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


@router.get("/cad/drawings", response_model=list[CADDrawingRead])
async def list_cad_drawings(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    result = await session.execute(
        select(CADDrawing).order_by(CADDrawing.created_at.desc())
    )
    return result.scalars().all()


@router.post(
    "/cad/drawings",
    response_model=CADDrawingRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_cad_drawing(
    title: str = Form(..., max_length=200),
    drawing_code: Optional[str] = Form(None, max_length=100),
    revision: Optional[str] = Form(None, max_length=40),
    category: str = Form("Planos", max_length=80),
    notes: Optional[str] = Form(None, max_length=5000),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    normalized_title = title.strip()
    if not normalized_title:
        raise HTTPException(status_code=400, detail="El nombre del archivo es obligatorio.")
    if not file.filename or not file.filename.strip():
        raise HTTPException(status_code=400, detail="Debe adjuntar un archivo.")

    suffix = safe_filename_suffix(file.filename, "")
    contents = await read_upload(
        file,
        allowed_extensions=CAD_ALLOWED_EXTENSIONS,
        max_bytes=CAD_MAX_UPLOAD_BYTES,
    )
    drawing_id = uuid.uuid4()
    stored_filename = f"{drawing_id.hex}{suffix}"
    destination = CAD_FILES_DIR / stored_filename

    try:
        destination.write_bytes(contents)
        drawing = CADDrawing(
            id=drawing_id,
            title=normalized_title,
            drawing_code=(drawing_code or "").strip() or None,
            revision=(revision or "").strip() or None,
            category=(category or "").strip() or "Planos",
            notes=(notes or "").strip() or None,
            original_filename=Path(file.filename.replace("\\", "/")).name,
            stored_filename=stored_filename,
            file_size=len(contents),
            uploaded_by=user.id,
        )
        session.add(drawing)
        await session.commit()
        await session.refresh(drawing)
        return drawing
    except Exception:
        await session.rollback()
        destination.unlink(missing_ok=True)
        logger.exception("Could not save CAD file metadata")
        raise


@router.get("/cad/drawings/{drawing_id}/file")
async def download_cad_drawing(
    drawing_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    drawing = await session.get(CADDrawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Archivo CAD no encontrado.")
    file_path = CAD_FILES_DIR / drawing.stored_filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="El archivo ya no está disponible.")
    return FileResponse(
        file_path,
        filename=drawing.original_filename,
        content_disposition_type="attachment",
    )


@router.delete("/cad/drawings/{drawing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cad_drawing(
    drawing_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    drawing = await session.get(CADDrawing, drawing_id)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Archivo CAD no encontrado.")
    file_path = CAD_FILES_DIR / drawing.stored_filename
    await session.delete(drawing)
    await session.commit()
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Could not remove CAD file %s", drawing.id)
        raise HTTPException(status_code=500, detail="No se pudo eliminar el archivo almacenado.")
