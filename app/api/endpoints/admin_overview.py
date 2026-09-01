import io
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

try:
    import pytesseract
    from PIL import Image
except Exception:  # pragma: no cover
    pytesseract = None
    Image = None

try:
    import easyocr
    import numpy as np
except Exception:  # pragma: no cover
    easyocr = None
    np = None

from app.core.auth import current_active_user
from app.core.db import get_async_session
from app.models.company import Company, CompanyRetention, InvoiceRetention
from app.models.user import User
from app.schemas.company import InvoiceRetentionRead, InvoiceRetentionUpdate

router = APIRouter()
_easyocr_reader = None

INVOICES_STATIC_DIR = Path(__file__).resolve().parents[2] / "static" / "invoices"
INVOICES_STATIC_DIR.mkdir(parents=True, exist_ok=True)
LEGACY_INVOICES_STATIC_DIR = Path(__file__).resolve().parents[1] / "static" / "invoices"
if LEGACY_INVOICES_STATIC_DIR.exists():
    for legacy_file in LEGACY_INVOICES_STATIC_DIR.iterdir():
        if legacy_file.is_file() and legacy_file.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
            target_file = INVOICES_STATIC_DIR / legacy_file.name
            if not target_file.exists():
                try:
                    target_file.write_bytes(legacy_file.read_bytes())
                except OSError:
                    pass


def _to_decimal(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0

    cleaned = text.replace(" ", "")
    if cleaned.count(",") and cleaned.count("."):
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        if cleaned.count(",") > 1:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except Exception:
        try:
            return float(text)
        except Exception:
            return 0.0


def _safe_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def extract_invoice_fields(text: str) -> Dict[str, Any]:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    money_pattern = r"(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+(?:[.,]\d{2})|\d+)"
    rif_match = re.search(r"(?:RIF|R\.I\.F|NIT)\s*[:\-]?\s*([A-Z]-\d{6,10}(?:-\d)?)", normalized, re.IGNORECASE)
    if not rif_match:
        rif_match = re.search(r"\b([A-Z0-9]-\d{6,10}-\d)\b", normalized, re.IGNORECASE)
    total_match = re.search(rf"\b(?:TOTAL\s*FACTURA|TOTAL\s*GENERAL|TOTAL\s*DE\s*LA\s*FACTURA)\b\s*[:\-]?\s*(?:Bs\.?\s*)?({money_pattern})", normalized, re.IGNORECASE)
    if not total_match:
        total_match = re.search(rf"\bTOTAL\b\s*[:\-]?\s*(?:Bs\.?\s*)?({money_pattern})", normalized, re.IGNORECASE)
    taxable_base_match = re.search(rf"(?:BASE\s*IMPONIBLE|BASE\s*IMPO\s*NIBLE|BASE\s*IMPOSIBLE|SUB\s*TOTAL|SUBTOTAL|BASE)\s*[:\-]?\s*(?:Bs\.?\s*)?({money_pattern})", normalized, re.IGNORECASE)
    iva_match = re.search(rf"(?:IVA|I\.V\.A\.|IMPUESTO\s*AL\s*VALOR)\s*[:\-]?\s*(?:Bs\.?\s*)?({money_pattern})", normalized, re.IGNORECASE)
    retention_match = re.search(rf"(?<!PORCENTAJE DE )(?:(?:RETENCION|RETENCIÓN|RETENTION|IMPUENTO\s*AL\s*VALOR|RETENCION\s*IVA|MONTO\s*RETENIDO))\s*[:\-]?\s*(?:Bs\.?\s*)?({money_pattern})", normalized, re.IGNORECASE)
    percent_match = re.search(r"(?:PORCENTAJE\s*DE\s*RETENCION|PORCENTAJE|%\s*RETENCION|RETENCION\s*\(%\)|RETENCION\s*\(%\)|RETENCION\s*\d)\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*%?", normalized, re.IGNORECASE)
    if not percent_match:
        percent_match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", normalized, re.IGNORECASE)
    fallback_numbers = re.findall(r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+(?:[.,]\d{2})", normalized)

    if not total_match and fallback_numbers:
        total_match = re.search(rf"({re.escape(fallback_numbers[0])})", normalized)

    if not percent_match and len(fallback_numbers) > 2:
        percent_value_candidate = fallback_numbers[-1]
        percent_match = re.search(rf"({re.escape(percent_value_candidate)})", normalized)
    if not percent_match:
        percent_match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", normalized)

    # The OCR fallback is ambiguous without labels. Exclude the percentage itself before
    # assigning any implicit fallback values so we do not mistake 5% for the IVA amount.
    percent_numeric = _to_decimal(percent_match.group(1)) if percent_match else None
    fallback_values_without_percent = [
        value for value in fallback_numbers
        if percent_numeric is None or abs(_to_decimal(value) - percent_numeric) > 1e-9
    ]

    if not taxable_base_match and len(fallback_values_without_percent) > 1 and not percent_match:
        taxable_base_match = re.search(rf"({re.escape(fallback_values_without_percent[1])})", normalized)
    if not iva_match and len(fallback_values_without_percent) > 2 and not percent_match:
        iva_match = re.search(rf"({re.escape(fallback_values_without_percent[2])})", normalized)
    if not retention_match and len(fallback_values_without_percent) > 1 and percent_match:
        # In the common unlabeled invoice format: total, retention, percentage.
        if not taxable_base_match and not iva_match:
            fallback_retention_candidate = fallback_values_without_percent[-1]
            retention_match = re.search(rf"({re.escape(fallback_retention_candidate)})", normalized)
    if not retention_match and len(fallback_numbers) > 3 and not percent_match:
        retention_match = re.search(rf"({re.escape(fallback_numbers[3])})", normalized)

    total_value = _to_decimal(total_match.group(1) if total_match else 0)
    taxable_base_value = _to_decimal(taxable_base_match.group(1) if taxable_base_match else 0)
    iva_value = _to_decimal(iva_match.group(1) if iva_match else 0)
    retention_value = _to_decimal(retention_match.group(1) if retention_match else 0)
    percent_value = float(str(percent_match.group(1) if percent_match else 0).replace(",", ".")) if percent_match else 0.0

    if not iva_match and taxable_base_value and total_value:
        iva_value = total_value - taxable_base_value
    if total_value and not retention_value and percent_value:
        if iva_value:
            retention_value = iva_value * (percent_value / 100)
        else:
            retention_value = total_value * (percent_value / 100)
        if retention_match and not iva_value and abs(retention_value) < 1e-9:
            retention_value = _to_decimal(retention_match.group(1) if retention_match else 0)
    elif total_value and retention_value and not percent_value:
        percent_value = (retention_value / iva_value) * 100 if iva_value else ((retention_value / total_value) * 100 if total_value else 0.0)
    elif total_value and retention_value and percent_value:
        retention_value = min(retention_value, iva_value or total_value)

    if not taxable_base_match and total_value and iva_value:
        taxable_base_value = total_value - iva_value

    supplier_match = re.search(
        r"(?:PROVEEDOR|EMPRESA|RAZON\s*SOCIAL|RAZÓN\s*SOCIAL|NOMBRE)\s*[:\-]?\s*(.+?)(?=\s+(?:RIF|R\.I\.F|NIT|DIRECCION|DIRECCIÓN|DOMICILIO|TOTAL|SUBTOTAL|BASE|IVA|RETENCION)\b|$)",
        normalized,
        re.IGNORECASE,
    )
    address_match = re.search(r"(?:DIRECCION|DIRECCIÓN|DOMICILIO)\s*[:\-]?\s*([A-ZÁÉÍÓÚÑ0-9#, .-]+)", normalized, re.IGNORECASE)

    return {
        "supplier_name": _safe_text(supplier_match.group(1) if supplier_match else "") or "Proveedor no identificado",
        "supplier_address": _safe_text(address_match.group(1) if address_match else "") or "",
        "rif": _safe_text(rif_match.group(1) if rif_match else "") or "",
        "total_amount": round(total_value, 2),
        "taxable_base": round(taxable_base_value, 2),
        "iva_amount": round(iva_value, 2),
        "retention_amount": round(retention_value, 2),
        "retention_percent": round(percent_value, 2),
    }


def _ocr_text_from_bytes(file_bytes: bytes, filename: str = "invoice") -> str:
    if not file_bytes:
        return ""

    if pytesseract is not None:
        try:
            from PIL import Image as PILImage
            from PIL import ImageOps

            tesseract_available = bool(shutil.which("tesseract"))
            if not tesseract_available:
                common_path = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
                if common_path.exists():
                    pytesseract.pytesseract.tesseract_cmd = str(common_path)
                    tesseract_available = True

            if tesseract_available:
                image = PILImage.open(io.BytesIO(file_bytes)).convert("L")
                image = ImageOps.autocontrast(image.resize((image.width * 2, image.height * 2)))
                results = []
                for config in ("--psm 6", "--psm 11"):
                    candidate = pytesseract.image_to_string(image, config=config)
                    if candidate and candidate.strip():
                        results.append(candidate.strip())
                if results:
                    return "\n".join(dict.fromkeys(results))
        except Exception:
            pass

    if easyocr is not None and np is not None:
        try:
            global _easyocr_reader
            if _easyocr_reader is None:
                _easyocr_reader = easyocr.Reader(["es", "en"], gpu=False, verbose=False)
            image = PILImage.open(io.BytesIO(file_bytes)).convert("RGB")
            image_array = np.asarray(image)
            lines = _easyocr_reader.readtext(image_array, detail=0, paragraph=False)
            text = "\n".join(str(line).strip() for line in lines if str(line).strip())
            if text:
                return text
        except Exception:
            pass

    return ""


async def _clean_invoice_with_deepseek(text: str, local_fields: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key or not text.strip():
        return local_fields

    schema = {
        "supplier_name": "string or null",
        "supplier_address": "string or null",
        "rif": "string or null",
        "total_amount": "number or null",
        "taxable_base": "number or null",
        "iva_amount": "number or null",
        "retention_amount": "number or null",
        "retention_percent": "number or null",
    }
    prompt = (
        "Extrae los datos de esta factura venezolana. Devuelve SOLO JSON válido con este esquema: "
        f"{json.dumps(schema, ensure_ascii=False)}. "
        "No inventes datos. Conserva los ceros explícitos. Convierte números con punto de miles "
        "y coma decimal a números. Si un campo no aparece claramente, usa null. "
        f"OCR:\n{text[:12000]}"
    )

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": "Eres un extractor contable estricto."},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            cleaned = json.loads(content)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return local_fields

    result = dict(local_fields)
    text_fields = {"supplier_name", "supplier_address"}
    for field in text_fields:
        value = cleaned.get(field)
        if isinstance(value, str) and value.strip():
            result[field] = value.strip()

    rif = cleaned.get("rif")
    if isinstance(rif, str) and re.fullmatch(r"[A-Z]-\d{6,10}(?:-\d)?", rif.strip(), re.IGNORECASE):
        result["rif"] = rif.strip().upper()

    numeric_fields = {
        "total_amount", "taxable_base", "iva_amount",
        "retention_amount", "retention_percent",
    }
    for field in numeric_fields:
        value = cleaned.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[field] = round(float(value), 2)
        elif isinstance(value, str) and value.strip():
            parsed_value = _to_decimal(value)
            if parsed_value >= 0:
                result[field] = round(parsed_value, 2)

    return result


async def _ocr_text_from_file(file: UploadFile) -> str:
    try:
        contents = await file.read()
    except Exception:
        return ""
    return _ocr_text_from_bytes(contents, file.filename or "invoice")


@router.get("/overview", status_code=status.HTTP_200_OK)
async def admin_overview(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    companies_result = await session.execute(select(Company))
    companies = companies_result.scalars().all()

    invoices_result = await session.execute(select(InvoiceRetention))
    invoices = invoices_result.scalars().all()
    company_retentions_result = await session.execute(select(CompanyRetention))
    company_retentions = company_retentions_result.scalars().all()

    now = datetime.utcnow()
    current_month = (now.year, now.month)
    total_retentions_month = sum(
        float(invoice.retention_amount or 0)
        for invoice in invoices
        if invoice.created_at and (invoice.created_at.year, invoice.created_at.month) == current_month
    )
    total_retentions_month += sum(
        float(retention.amount or 0)
        for retention in company_retentions
        if retention.created_at and (retention.created_at.year, retention.created_at.month) == current_month
    )
    pending_amount = sum(
        float(invoice.retention_amount or 0)
        for invoice in invoices
        if (invoice.retention_status or "").lower() in {"pendiente", "pending"}
    )
    pending_amount += sum(
        float(retention.amount or 0)
        for retention in company_retentions
        if (retention.status or "").lower() in {"pendiente", "pending"}
    )

    trend_by_day = {}
    for invoice in invoices:
        if not invoice.created_at:
            continue
        day = invoice.created_at.date()
        trend_by_day[day.isoformat()] = trend_by_day.get(day.isoformat(), 0) + float(invoice.retention_amount or 0)
    for retention in company_retentions:
        if not retention.created_at:
            continue
        day = retention.created_at.date()
        trend_by_day[day.isoformat()] = trend_by_day.get(day.isoformat(), 0) + float(retention.amount or 0)

    retention_trend = [
        {
            "date": (now.date() - timedelta(days=days_ago)).isoformat(),
            "value": round(trend_by_day.get((now.date() - timedelta(days=days_ago)).isoformat(), 0), 2),
        }
        for days_ago in range(89, -1, -1)
    ]
    last_7 = [item["value"] for item in retention_trend[-7:]]

    return {
        "total_retentions_month": total_retentions_month,
        "pending_amount": pending_amount,
        "companies_count": len(companies),
        "invoices_processed": len(invoices),
        "total_peajes": 0,
        "retentions_last_7": last_7,
        "retentions_trend": retention_trend,
    }


@router.get("/activity", status_code=status.HTTP_200_OK)
async def admin_activity(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    companies_result = await session.execute(select(Company).order_by(Company.updated_at.desc()).limit(5))
    companies = companies_result.scalars().all()

    items: List[Dict[str, Any]] = []
    for company in companies:
        items.append({
            "date": company.updated_at.isoformat(),
            "action": "Empresa actualizada",
            "actor": company.contact_name or "Sistema",
            "detail": company.name,
        })

    if not items:
        items.append({
            "date": datetime.utcnow().isoformat(),
            "action": "No hay actividad reciente",
            "actor": "Sistema",
            "detail": "El módulo de administración aún no registró movimientos.",
        })

    return items


@router.get("/invoices", response_model=List[InvoiceRetentionRead], status_code=status.HTTP_200_OK)
async def list_invoices(
    rif: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    stmt = select(InvoiceRetention).order_by(InvoiceRetention.created_at.desc())
    if rif:
        stmt = stmt.where(InvoiceRetention.rif.ilike(f"%{rif}%"))
    if status:
        normalized_status = status.lower()
        if normalized_status in {"collected", "cobrada", "cobradas"}:
            stmt = stmt.where(InvoiceRetention.collected.is_(True))
        elif normalized_status in {"pending", "pendiente", "pendientes"}:
            stmt = stmt.where(InvoiceRetention.collected.is_(False))
        else:
            stmt = stmt.where(InvoiceRetention.retention_status.ilike(f"%{status}%"))
    result = await session.execute(stmt)
    return result.scalars().all()


@router.post("/invoices/upload", response_model=InvoiceRetentionRead, status_code=status.HTTP_201_CREATED)
async def upload_invoice(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Debes adjuntar una factura.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="La factura está vacía.")

    extension = Path(file.filename).suffix.lower()
    safe_name = f"invoice_{uuid.uuid4()}{extension}"
    uploaded_path = INVOICES_STATIC_DIR / safe_name
    uploaded_path.write_bytes(file_bytes)

    text = ""
    if extension.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
        text = _ocr_text_from_bytes(file_bytes, file.filename)
    else:
        text = file_bytes.decode("utf-8", errors="ignore")

    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail="No se pudo leer la factura. Instala Tesseract OCR o verifica que la imagen sea legible.",
        )

    parsed = await _clean_invoice_with_deepseek(text, extract_invoice_fields(text))
    company_match = None
    if parsed.get("rif"):
        company_match = await session.execute(select(Company).where(Company.rif.ilike(f"%{parsed['rif']}%")))
        company_match = company_match.scalar_one_or_none()

    invoice = InvoiceRetention(
        company_id=company_match.id if company_match else None,
        supplier_name=parsed.get("supplier_name") or "Proveedor no identificado",
        supplier_address=parsed.get("supplier_address"),
        rif=parsed.get("rif"),
        total_amount=float(parsed.get("total_amount") or 0),
        taxable_base=float(parsed.get("taxable_base") or 0),
        iva_amount=float(parsed.get("iva_amount") or 0),
        retention_amount=float(parsed.get("retention_amount") or 0),
        retention_percent=float(parsed.get("retention_percent") or 0),
        retention_status="pending",
        collected=False,
        image_url=f"/static/invoices/{safe_name}",
    )
    session.add(invoice)
    await session.commit()
    await session.refresh(invoice)
    return invoice


@router.patch("/invoices/{invoice_id}/status", response_model=InvoiceRetentionRead)
async def update_invoice_status(
    invoice_id: uuid.UUID,
    supplier_name: Optional[str] = Form(None),
    supplier_address: Optional[str] = Form(None),
    rif: Optional[str] = Form(None),
    total_amount: Optional[float] = Form(None),
    taxable_base: Optional[float] = Form(None),
    iva_amount: Optional[float] = Form(None),
    retention_amount: Optional[float] = Form(None),
    retention_percent: Optional[float] = Form(None),
    company_id: Optional[uuid.UUID] = Form(None),
    retention_status: Optional[str] = Form(None),
    collected: Optional[bool] = Form(None),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    invoice = await session.get(InvoiceRetention, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")

    if company_id is not None:
        company = await session.get(Company, company_id)
        if not company:
            raise HTTPException(status_code=404, detail="Empresa no encontrada.")
        invoice.company_id = company_id

    if supplier_name is not None:
        invoice.supplier_name = supplier_name
    if supplier_address is not None:
        invoice.supplier_address = supplier_address
    if rif is not None:
        invoice.rif = rif
    if total_amount is not None:
        invoice.total_amount = float(total_amount)
    if taxable_base is not None:
        invoice.taxable_base = float(taxable_base)
    if iva_amount is not None:
        invoice.iva_amount = float(iva_amount)
    if retention_amount is not None:
        invoice.retention_amount = float(retention_amount)
    if retention_percent is not None:
        invoice.retention_percent = float(retention_percent)
    if retention_status is not None:
        invoice.retention_status = retention_status
    if collected is not None:
        invoice.collected = bool(collected)
        invoice.retention_status = "collected" if invoice.collected else "pending"

    invoice.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(invoice)
    return invoice
