import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import Column, Text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel, select

from app.api.endpoints.safety_permits import extract_text
from app.core.auth import current_active_user
from app.core.audit import add_audit_entry
from app.core.db import get_async_session
from app.core.storage import MAX_UPLOAD_BYTES, media_dir, read_upload, safe_filename_suffix
from app.models.settings import SystemSettings
from app.models.user import User

router = APIRouter()
DOCUMENTS_DIR = media_dir("documents")
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".txt"}


class ScannedDocument(SQLModel, table=True):
    __tablename__ = "scanned_documents"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    uploaded_by: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    original_filename: str = Field(nullable=False)
    stored_filename: str = Field(nullable=False)
    extracted_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    document_type: Optional[str] = Field(default=None, max_length=255)
    information: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    expiration_date: Optional[date] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class DocumentRead(BaseModel):
    id: uuid.UUID
    original_filename: str
    file_url: str
    extracted_text: str
    document_type: str
    information: str
    expiration_date: Optional[date]
    status: str
    created_at: datetime


class DocumentMetadataUpdate(BaseModel):
    document_type: Optional[str] = None
    information: Optional[str] = None
    expiration_date: Optional[date] = None


MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
DATE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{2,4})(?!\d)"
    r"|(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"
    r"|(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})",
    re.IGNORECASE,
)
EXPIRATION_LABEL = re.compile(
    r"\b(?:fecha\s+de\s+)?(?:vencimiento|venc(?:e|er)?|expiraci[oó]n|expira|caducidad|caduca)\b"
    r"|\b(?:vigencia|v[aá]lid[oa])\s+(?:hasta|al)\b"
    r"|\bfecha\s+l[ií]mite\b",
    re.IGNORECASE,
)
DOCUMENT_TYPE_RULES = (
    ("Permiso de trabajo", ("permiso de trabajo", "work permit")),
    ("Licencia", ("licencia", "license")),
    ("Póliza de seguro", ("póliza", "poliza", "seguro de responsabilidad")),
    ("Contrato", ("contrato", "contract")),
    ("Certificado", ("certificado", "certificate")),
    ("Cédula de identidad", ("cédula", "cedula", "documento de identidad")),
    ("Pasaporte", ("pasaporte", "passport")),
    ("Factura", ("factura", "invoice")),
    ("Constancia", ("constancia", "comprobante")),
    ("ART", ("análisis de riesgo", "analisis de riesgo", "art")),
    ("VIT", ("vit", "vigilancia de salud")),
)


def identify_document_type(text: str, filename: str = "") -> str:
    normalized = f"{filename} {text}".casefold()
    for document_type, keywords in DOCUMENT_TYPE_RULES:
        if any(
            re.search(rf"\b{re.escape(keyword)}\b", normalized) if keyword in {"art", "vit"} else keyword in normalized
            for keyword in keywords
        ):
            return document_type
    return "No identificado"


def _parse_date(match: re.Match[str]) -> Optional[date]:
    try:
        if match.group(1):
            day, month, year = (int(match.group(index)) for index in (1, 2, 3))
            if year < 100:
                year += 2000 if year < 70 else 1900
            return date(year, month, day)
        if match.group(4):
            year, month, day = (int(match.group(index)) for index in (4, 5, 6))
            return date(year, month, day)
        day = int(match.group(7))
        month = MONTHS.get(match.group(8).casefold())
        year = int(match.group(9))
        return date(year, month, day) if month else None
    except (ValueError, TypeError):
        return None


def extract_expiration_date(text: str) -> Optional[date]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    for index, line in enumerate(lines):
        label = EXPIRATION_LABEL.search(line)
        if not label:
            continue
        candidates = [line[label.end():]]
        if index + 1 < len(lines):
            candidates.append(lines[index + 1])
        for candidate in candidates:
            for match in DATE_PATTERN.finditer(candidate):
                parsed = _parse_date(match)
                if parsed:
                    return parsed
    return None


def _document_status(expiration_date: Optional[date]) -> str:
    if expiration_date is None:
        return "sin_fecha"
    days_remaining = (expiration_date - date.today()).days
    if days_remaining < 0:
        return "vencido"
    if days_remaining <= 30:
        return "por_vencer"
    return "vigente"


def _document_read(document: ScannedDocument) -> DocumentRead:
    return DocumentRead(
        id=document.id,
        original_filename=document.original_filename,
        file_url=f"/api/documents/{document.id}/file",
        extracted_text=document.extracted_text,
        document_type=document.document_type or identify_document_type(document.extracted_text, document.original_filename),
        information=document.information if document.information is not None else document.extracted_text,
        expiration_date=document.expiration_date,
        status=_document_status(document.expiration_date),
        created_at=document.created_at,
    )


@router.get("", response_model=List[DocumentRead])
async def list_documents(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    result = await session.execute(select(ScannedDocument).order_by(ScannedDocument.created_at.desc()))
    return [_document_read(document) for document in result.scalars().all()]


@router.post("/scan", response_model=List[DocumentRead], status_code=status.HTTP_201_CREATED)
async def scan_documents(
    files: List[UploadFile] = File(...),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    saved_documents = []
    created_paths = []
    try:
        system_settings = await session.get(SystemSettings, "general")
        max_upload_bytes = (system_settings.max_document_upload_mb * 1024 * 1024) if system_settings else MAX_UPLOAD_BYTES
        preferred_ocr_language = system_settings.ocr_language if system_settings else "auto"
        for file in files:
            original_filename = (file.filename or "").strip()
            suffix = safe_filename_suffix(original_filename, "")
            if suffix not in ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=415, detail=f"Formato no permitido: {original_filename}")
            contents = await read_upload(file, allowed_extensions=ALLOWED_EXTENSIONS, max_bytes=max_upload_bytes)
            document_id = uuid.uuid4()
            stored_filename = f"{document_id.hex}{suffix}"
            destination = DOCUMENTS_DIR / stored_filename
            destination.write_bytes(contents)
            created_paths.append(destination)

            extracted_text = extract_text(original_filename, contents, preferred_ocr_language)
            document = ScannedDocument(
                id=document_id,
                uploaded_by=user.id,
                original_filename=Path(original_filename).name,
                stored_filename=stored_filename,
                extracted_text=extracted_text,
                document_type=identify_document_type(extracted_text, original_filename),
                information=extracted_text,
                expiration_date=extract_expiration_date(extracted_text),
            )
            session.add(document)
            add_audit_entry(
                session,
                actor=user,
                action="document.scanned",
                entity_type="document",
                entity_id=str(document.id),
                details={"filename": document.original_filename, "document_type": document.document_type, "expiration_date_detected": document.expiration_date is not None},
            )
            saved_documents.append(document)

        await session.commit()
        for document in saved_documents:
            await session.refresh(document)
        return [_document_read(document) for document in saved_documents]
    except Exception:
        await session.rollback()
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise


@router.patch("/{document_id}", response_model=DocumentRead)
async def update_document_expiration(
    document_id: uuid.UUID,
    payload: DocumentMetadataUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    document = await session.get(ScannedDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    if "document_type" in payload.model_fields_set:
        document.document_type = (payload.document_type or "").strip() or "No identificado"
    if "information" in payload.model_fields_set:
        document.information = payload.information or ""
    if "expiration_date" in payload.model_fields_set:
        document.expiration_date = payload.expiration_date
    add_audit_entry(
        session,
        actor=user,
        action="document.metadata.updated",
        entity_type="document",
        entity_id=str(document.id),
        details={"fields": list(payload.model_fields_set)},
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return _document_read(document)


@router.get("/{document_id}/file")
async def get_document_file(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    document = await session.get(ScannedDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    file_path = DOCUMENTS_DIR / document.stored_filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="El archivo original ya no está disponible.")
    return FileResponse(file_path, filename=document.original_filename, content_disposition_type="inline")