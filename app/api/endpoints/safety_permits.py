import io
import importlib.util
import pkgutil
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Field, SQLModel, select
from sqlalchemy import Column, Text
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image, ImageEnhance, ImageOps

from app.core.auth import current_active_user
from app.core.db import get_async_session
from app.models.user import User

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    if not hasattr(pkgutil, "find_loader"):
        pkgutil.find_loader = lambda module_name: importlib.util.find_spec(module_name)
    import pytesseract
    for tesseract_path in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if Path(tesseract_path).exists():
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
            break
except Exception:
    pytesseract = None

try:
    import cv2
    import numpy as np
except Exception:
    cv2 = None
    np = None

PERMIT_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "security" / "permits"
PERMIT_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()


class SafetyPermit(SQLModel, table=True):
    __tablename__ = "safety_permit"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    uploaded_by: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    filename: str
    source_path: str
    extracted_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    activity: Optional[str] = None
    permit_number: Optional[str] = None
    work_area: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    personnel: Optional[str] = None
    risks: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.now)


class PermitRisk(BaseModel):
    name: str
    severity: str
    reason: str
    controls: List[str]


class PermitPersonnel(BaseModel):
    id: uuid.UUID
    username: str
    name: Optional[str] = None
    email: str
    confidence: str


class PermitAnalysisRead(BaseModel):
    id: uuid.UUID
    filename: str
    source_path: str
    extracted_text: str
    permit_number: Optional[str] = None
    permit_type: Optional[str] = None
    permit_date: Optional[str] = None
    permit_time: Optional[str] = None
    valid_until: Optional[str] = None
    shift: Optional[str] = None
    equipment: Optional[str] = None
    description: Optional[str] = None
    risk_analysis_number: Optional[str] = None
    work_procedure_number: Optional[str] = None
    contractor: Optional[str] = None
    personnel_count: Optional[str] = None
    signatories: List[str]
    activity: Optional[str] = None
    work_area: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    personnel: List[PermitPersonnel]
    risks: List[PermitRisk]
    created_at: datetime


class SafetyDocumentAnalysisRead(BaseModel):
    document_type: str
    filename: str
    source_path: str
    extracted_text: str
    activity: Optional[str] = None
    work_area: Optional[str] = None
    risks: List[PermitRisk]


RISK_RULES = [
    ("Trabajo en altura", ["altura", "andamio", "escalera", "arnés", "arnes"], "Caídas a distinto nivel.", ["Arnés y línea de vida", "Inspección de andamios y escaleras", "Delimitar el área inferior"]),
    ("Trabajo en caliente", ["soldadura", "oxicorte", "esmeril", "trabajo en caliente", "chispas"], "Incendio, quemaduras y proyección de partículas.", ["Permiso de trabajo en caliente", "Extintor disponible", "Retirar materiales combustibles"]),
    ("Riesgo eléctrico", ["eléctr", "tablero", "cableado", "energizado", "voltaje"], "Contacto eléctrico y arco eléctrico.", ["Bloqueo y etiquetado", "Verificar ausencia de tensión", "EPP dieléctrico"]),
    ("Espacio confinado", ["espacio confinado", "espacios confinados", "confinado", "confinados", "tanque", "pozo", "silo", "alcantarilla"], "Atmósfera peligrosa, asfixia o rescate complejo.", ["Medición atmosférica", "Vigía permanente", "Plan de rescate"]),
    ("Izamiento de cargas", ["izamiento", "grúa", "grua", "carga suspendida", "montacargas"], "Golpe, atrapamiento o caída de carga.", ["Inspeccionar accesorios", "Señalero designado", "Aislar el radio de giro"]),
    ("Sustancias peligrosas", ["químic", "solvente", "ácido", "combustible", "gas"], "Exposición, incendio o derrame.", ["Hoja de seguridad disponible", "Guantes y protección facial", "Control de derrames"]),
    ("Excavación", ["excavación", "zanja", "movimiento de tierra"], "Derrumbe, caída o contacto con servicios enterrados.", ["Entibar o taluzar", "Localizar servicios", "Señalizar el perímetro"]),
]


def extract_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        if PdfReader is None:
            return ""
        try:
            reader = PdfReader(io.BytesIO(content))
            return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception:
            return ""
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}:
        if pytesseract is None:
            return ""
        try:
            image = ImageOps.exif_transpose(Image.open(io.BytesIO(content)).convert("L"))
            scale = max(1, min(3, 2200 // max(image.width, 1)))
            if scale > 1:
                image = image.resize((image.width * scale, image.height * scale))
            languages = set(pytesseract.get_languages(config=""))
            tessdata_dir = Path(__file__).resolve().parent.parent.parent / "static" / "security" / "tessdata"
            spanish_model = tessdata_dir / "spa.traineddata"
            tessdata_arg = f"--tessdata-dir {tessdata_dir.as_posix()}" if spanish_model.exists() else ""
            ocr_language = "spa" if spanish_model.exists() else "eng" if "eng" in languages else "osd"
            variants = [image, ImageEnhance.Contrast(image).enhance(2.2)]
            variants.append(image.point(lambda pixel: 0 if pixel < 180 else 255))
            if cv2 is not None and np is not None:
                cv_image = np.array(image)
                denoised = cv2.fastNlMeansDenoising(cv_image, None, 10, 7, 21)
                adaptive = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11)
                variants.append(Image.fromarray(adaptive))
            readings = []
            for variant in variants:
                for page_mode in (6, 11):
                    config = f"{tessdata_arg} --psm {page_mode} -c user_defined_dpi=300"
                    data = pytesseract.image_to_data(variant, lang=ocr_language, config=config, output_type=pytesseract.Output.DICT)
                    line_words: Dict[tuple, List[str]] = {}
                    for index, word in enumerate(data.get("text", [])):
                        cleaned_word = word.strip() if word else ""
                        if not cleaned_word:
                            continue
                        line_key = (
                            data.get("block_num", [0])[index],
                            data.get("par_num", [0])[index],
                            data.get("line_num", [0])[index],
                        )
                        line_words.setdefault(line_key, []).append(cleaned_word)
                    words = [" ".join(line).strip() for line in line_words.values()]
                    confidences = [float(value) for value in data.get("conf", []) if str(value).strip() not in {"", "-1"}]
                    reading = "\n".join(words).strip()
                    confidence = sum(confidences) / len(confidences) if confidences else 0.0
                    if reading:
                        readings.append((reading, confidence))
            if not readings:
                return ""
            keywords = ("permiso", "actividad", "trabajo", "riesgo", "procedimiento", "contratista", "equipo", "personas", "espacio", "confinado", "eléctrico", "soldadura", "ubicación")
            best_reading = max(
                readings,
                key=lambda candidate: candidate[1] * 100
                + sum(candidate[0].casefold().count(keyword) for keyword in keywords) * 35
                + min(len(candidate[0]), 100),
            )[0]
            useful_labels = ("permiso", "fecha", "hora", "actividad", "área", "area", "equipo", "descripción", "descripcion", "riesgo", "procedimiento", "contratista", "personas", "validez", "vigencia")
            merged_lines = list(dict.fromkeys(best_reading.splitlines()))
            for reading, _confidence in sorted(readings, key=lambda candidate: candidate[1], reverse=True):
                reading_lines = [line.strip() for line in reading.splitlines()]
                for line_index, line in enumerate(reading_lines):
                    if line.strip() and any(label in line.casefold() for label in useful_labels):
                        if line.strip() not in merged_lines:
                            merged_lines.append(line.strip())
                        if line_index + 1 < len(reading_lines) and reading_lines[line_index + 1] not in merged_lines:
                            merged_lines.append(reading_lines[line_index + 1])
            return "\n".join(merged_lines)
        except Exception as exc:
            print(f"OCR del permiso no disponible: {exc}")
            return ""
    return content.decode("utf-8", errors="ignore").strip()


def field_from_text(text: str, labels: List[str]) -> Optional[str]:
    normalized_text = re.sub(r"\bfecha\s+(?:ida|idao|lda)\b", "FECHA", text, flags=re.IGNORECASE)
    normalized_text = re.sub(r"\bhora\s+(?:de\s+)?(?:du[cç]io|in[ií]cio|inicio)\b", "HORA", normalized_text, flags=re.IGNORECASE)
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    next_label = r"(?:(?:\d+\s*[.\-]\s*)?(?:an[aá]lisis|permiso|n[úu]mero|nro?\.?|fecha|d[ií]a|hora|actividad|[áa]rea|ubicaci[oó]n|inicio|fin|equipo|descripci[oó]n|riesgo|procedimiento|contratista|personas|validez|vigencia|firma(?:nte)?|emisor|receptor|ejecutor|responsable))"
    match = re.search(rf"(?:^|[\n|])[^\n|]*?\b(?:{label_pattern})\b\s*(?:[:#º°-]|\s)\s*([^\n|]+)", normalized_text, re.IGNORECASE)
    if match:
        value = re.split(rf"\s+(?={next_label})", match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
        value = re.sub(r"\s+", " ", value).strip(" .:-")
        if value:
            return value[:255]

    lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if re.search(rf"\b(?:{label_pattern})\b", line, re.IGNORECASE) and index + 1 < len(lines):
            next_line = lines[index + 1]
            if not re.match(rf"^\s*(?:{next_label})\b", next_line, re.IGNORECASE):
                return re.sub(r"\s+", " ", next_line).strip(" .:-")[:255]
    return None


def extract_permit_number(text: str) -> Optional[str]:
    normalized_text = re.sub(r"\bN\s*[º°o]\b", "N°", text, flags=re.IGNORECASE)
    patterns = [
        r"(?:n[úu]mero|nro?\.?|no\.?|#|n°)\s*(?:de\s*)?(?:permiso)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{0,20})\b",
        r"(?:permiso|permit)\s+de\s+trabajo\s+n[°ºo.]\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{1,20})\b",
        r"(?:permiso|permit)\s+de\s+trabajo\s+n\s+([A-Z0-9][A-Z0-9./_-]{1,20})\b",
        r"(?:permiso|permit)\s+(?:n[úu]mero|nro?\.?|no\.?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{1,20})\b",
    ]
    for line in normalized_text.splitlines():
        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                candidate = match.group(1).strip()[:100]
                if any(character.isdigit() for character in candidate) and not re.search(r"\b(?:de|trabajo|permiso|autorizaci[oó]n)\b", candidate, re.IGNORECASE):
                    return candidate
    match = re.search(r"\b(PI\s*[-–—]?\s*\d{4,})\b", normalized_text, re.IGNORECASE)
    if match:
        return re.sub(r"\s+", "", match.group(1)).replace("–", "-").replace("—", "-").upper()
    return None


def extract_permit_date(text: str) -> Optional[str]:
    labeled = field_from_text(text, ["fecha del permiso", "fecha", "día", "dia"])
    if labeled:
        match = re.search(r"\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*(?:\d{4}|\d{2})(?!\d)|\d{1,2}\s+de\s+[a-záéíóú]+\s+de\s+\d{4}", labeled, re.IGNORECASE)
        if match:
            return match.group(0)
    match = re.search(r"(?<!\d)(\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*(?:\d{4}|\d{2})(?!\d))", text)
    if match:
        return match.group(1)
    match = re.search(r"\b(\d{1,2}\s+de\s+[a-záéíóú]+\s+de\s+\d{4})\b", text, re.IGNORECASE)
    return match.group(1) if match else None


def extract_permit_time(text: str) -> Optional[str]:
    labeled = field_from_text(text, ["hora del permiso", "hora"])
    if labeled:
        compact_digits = re.sub(r"\D", "", labeled)
        if len(compact_digits) == 3:
            if compact_digits.startswith("0"):
                return f"{int(compact_digits[:2]):02d}:00"
            return f"{int(compact_digits[0]):02d}:{compact_digits[1:]}"
        match = re.search(r"(?:[01]?\d|2[0-3])[:.]\d{2}(?:\s?[ap]\.?m\.?)?", labeled, re.IGNORECASE)
        if match:
            return match.group(0)
        compact = re.search(r"(?<!\d)([01]?\d|2[0-3])([0-5]\d?)(?!\d)", re.sub(r"\D", "", labeled))
        if compact:
            hour, minutes = compact.groups()
            meridiem = re.search(r"\b([ap])\.?\s*m\.?\b", labeled, re.IGNORECASE)
            suffix = f" {meridiem.group(1).upper()}M" if meridiem else ""
            return f"{int(hour):02d}:{int(minutes):02d}{suffix}"
    match = re.search(r"\b(?:a las?\s*)?((?:[01]?\d|2[0-3])\s*[:.]?\s*\d{2}(?:\s?[ap]\.?m\.?)?)\b", text, re.IGNORECASE)
    if not match:
        compact_digits = re.sub(r"\D", "", text)
        if len(compact_digits) == 3:
            if compact_digits.startswith("0"):
                return f"{int(compact_digits[:2]):02d}:00"
            return f"{int(compact_digits[0]):02d}:{compact_digits[1:]}"
        compact = re.search(r"(?<!\d)([01]?\d)([0-5]\d?)(?!\d)", re.sub(r"\D", "", text))
        if not compact:
            return None
        hour, minutes = compact.groups()
        meridiem = re.search(r"\b([ap])\.?\s*m\.?\b", text, re.IGNORECASE)
        suffix = f" {meridiem.group(1).upper()}M" if meridiem else ""
        return f"{int(hour):02d}:{int(minutes):02d}{suffix}"
    value = re.sub(r"\s*[:.]\s*|\s+", ":", match.group(1).strip())
    return value


def extract_permit_structured_fields(text: str, risks: List[PermitRisk]) -> Dict[str, Optional[str]]:
    normalized = text.replace("\u00a0", " ")
    permit_type = None
    if re.search(r"en\s+fr[ií]o", normalized, re.IGNORECASE):
        permit_type = "Trabajo en frío"
    elif re.search(r"en\s+caliente", normalized, re.IGNORECASE):
        permit_type = "Trabajo en caliente"

    description = field_from_text(normalized, ["descripción de los trabajos", "descripcion de los trabajos", "descripción", "descripcion", "trabajo a realizar"])
    equipment = field_from_text(normalized, ["equipo", "equipos", "maquinaria"])
    contractor = field_from_text(normalized, ["contratista", "empresa contratista"])
    personnel_count = field_from_text(normalized, ["n° de personas", "nº de personas", "numero de personas", "número de personas"])
    personnel_count = personnel_count or (re.search(r"(?:n[°ºo*]|mo)\s*de\s*personas\s*[:#-]?\s*(\d{1,2})", normalized, re.IGNORECASE).group(1) if re.search(r"(?:n[°ºo*]|mo)\s*de\s*personas\s*[:#-]?\s*(\d{1,2})", normalized, re.IGNORECASE) else None)
    risk_match = re.search(r"(?:an[aá]lisis|am[aá]lisiz|an[aá]lisiz)\s+de\s+riesgos\s*(?:n[°ºo*]?\.?\s*)?[:#-]?\s*([A-Za-z0-9./_-]+)", normalized, re.IGNORECASE)
    procedure_match = re.search(r"procedimiento\s+de\s+trabajo\s*(?:n[°ºo*]?\.?\s*)?[:#-]?\s*([A-Za-z0-9./_-]+)", normalized, re.IGNORECASE)
    risk_number = risk_match.group(1) if risk_match else None
    procedure_number = procedure_match.group(1) if procedure_match else None
    valid_until = field_from_text(normalized, ["validez hasta", "válido hasta", "valido hasta", "vigencia hasta"])

    if personnel_count:
        count = re.search(r"\d+", personnel_count)
        personnel_count = count.group(0) if count else personnel_count
    if contractor:
        if re.search(r"soland", contractor, re.IGNORECASE):
            contractor = "Soland"
        contractor = re.split(r"\s+(?:n[úu]mero|no\.?|n[°º*]|personas|fecha|hora)\b", contractor, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .:-")
    if description and re.match(r"^(?:(?:\d+\s*[.\-]\s*)?(?:an[aá]lisis|procedimiento|de|del|rr|r{1,2}))\b", description, re.IGNORECASE):
        description = None
    if risk_number and not re.search(r"\d", risk_number):
        risk_number = None
    if procedure_number and (procedure_number.casefold() in {"n", "no", "nr", "nn"} or not re.search(r"\d", procedure_number)):
        procedure_number = None
    if valid_until and not re.search(r"\b(?:[01]?\d|2[0-3])\s*[:.]\s*[0-5]?\d\b", valid_until):
        valid_until = None
    return {
        "permit_type": permit_type,
        "equipment": equipment,
        "description": description,
        "risk_analysis_number": risk_number,
        "work_procedure_number": procedure_number,
        "contractor": contractor,
        "personnel_count": personnel_count,
        "valid_until": valid_until,
        "shift": ("AM" if re.search(r"\bAM\b", normalized, re.IGNORECASE) else "PM" if re.search(r"\bPM\b", normalized, re.IGNORECASE) else None),
        "activity": field_from_text(normalized, ["actividad", "trabajo a realizar"]) or permit_type,
    }


def extract_permit_fields_from_image(filename: str, content: bytes) -> Dict[str, Optional[str]]:
    if pytesseract is None or Path(filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}:
        return {"number": None, "date": None, "time": None}
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(content)).convert("L"))
        width, height = image.size
        regions = {
            "number": image.crop((int(width * 0.63), int(height * 0.03), width, int(height * 0.25))),
            "date": image.crop((0, int(height * 0.42), int(width * 0.45), int(height * 0.66))),
            "time": image.crop((int(width * 0.42), int(height * 0.42), int(width * 0.9), int(height * 0.68))),
        }
        readings: Dict[str, List[str]] = {key: [] for key in regions}
        for key, region in regions.items():
            scale = 4
            enlarged = region.resize((region.width * scale, region.height * scale))
            for variant in (enlarged, ImageEnhance.Contrast(enlarged).enhance(2.4)):
                for psm in (6, 11):
                    value = pytesseract.image_to_string(variant, config=f"--psm {psm}").strip()
                    if value:
                        readings[key].append(value)
        number = next((value for value in readings["number"] if re.search(r"\bPI\s*[-–—]?\s*\d{4,}\b", value, re.IGNORECASE)), None)
        date = next((value for value in readings["date"] if re.search(r"\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}", value)), None)
        time = next((value for value in readings["time"] if re.search(r"\b(?:[01]?\d|2[0-3])\s*[:.]?\s*\d{2}\b", value)), None)
        return {
            "number": extract_permit_number(number or ""),
            "date": extract_permit_date(date or ""),
            "time": extract_permit_time(time or ""),
        }
    except Exception as exc:
        print(f"OCR por regiones no disponible: {exc}")
        return {"number": None, "date": None, "time": None}


def extract_signatories(text: str) -> List[str]:
    signatories = []
    for line in text.splitlines():
        match = re.search(r"(?:firma(?:nte)?|emisor|receptor|ejecutor)\s*[:#-]\s*(.+)$", line, re.IGNORECASE)
        if match:
            value = match.group(1).strip()[:120]
            if value and value.casefold() not in {item.casefold() for item in signatories}:
                signatories.append(value)
    return signatories[:6]


def infer_risks(text: str) -> List[PermitRisk]:
    normalized = re.sub(r"[^a-záéíóúüñ0-9 ]", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized)
    risks = []
    for name, keywords, reason, controls in RISK_RULES:
        if any(keyword in normalized for keyword in keywords):
            severity = "Alta" if name in {"Espacio confinado", "Riesgo eléctrico", "Trabajo en altura"} else "Media"
            risks.append(PermitRisk(name=name, severity=severity, reason=reason, controls=controls))
    return risks


def infer_activity(text: str, risks: List[PermitRisk]) -> Optional[str]:
    normalized = text.casefold()
    if "confinad" in normalized or "tanque" in normalized or "pozo" in normalized:
        return "Trabajo en espacios confinados"
    if "soldad" in normalized or "oxicorte" in normalized or "esmeril" in normalized:
        return "Trabajo en caliente"
    if "altura" in normalized or "andamio" in normalized or "arn" in normalized:
        return "Trabajo en altura"
    if "eléct" in normalized or "electric" in normalized or "voltaje" in normalized:
        return "Trabajo eléctrico"
    return risks[0].name if risks else None


def match_personnel(text: str, users: List[User]) -> List[PermitPersonnel]:
    normalized = text.casefold()
    matches = []
    for user in users:
        candidates = [user.username, user.email, user.nombre_completo or "", user.cargo or ""]
        found = next((candidate for candidate in candidates if candidate and candidate.casefold() in normalized), None)
        if found:
            matches.append(PermitPersonnel(id=user.id, username=user.username, name=user.nombre_completo, email=user.email, confidence="Alta" if found != user.cargo else "Media"))
    return matches


def to_read(permit: SafetyPermit, personnel: List[PermitPersonnel], risks: List[PermitRisk]) -> PermitAnalysisRead:
    fields = extract_permit_structured_fields(permit.extracted_text, risks)
    signatories = [person.name or person.username for person in personnel]
    return PermitAnalysisRead(
        id=permit.id, filename=permit.filename, source_path=permit.source_path,
        extracted_text=permit.extracted_text, permit_number=permit.permit_number,
        permit_type=fields["permit_type"], equipment=fields["equipment"], description=fields["description"],
        risk_analysis_number=fields["risk_analysis_number"], work_procedure_number=fields["work_procedure_number"],
        contractor=fields["contractor"], personnel_count=fields["personnel_count"], valid_until=fields["valid_until"],
        permit_date=extract_permit_date(permit.extracted_text),
        permit_time=extract_permit_time(permit.extracted_text),
        shift=fields["shift"], signatories=signatories,
        activity=permit.activity or fields["activity"], work_area=permit.work_area, start_at=permit.start_at,
        end_at=permit.end_at, personnel=personnel, risks=risks, created_at=permit.created_at,
    )


@router.post("/permit/analyze", response_model=PermitAnalysisRead, status_code=status.HTTP_201_CREATED)
async def analyze_safety_permit(
    file: UploadFile = File(...),
    activity: Optional[str] = Form(None),
    work_area: Optional[str] = Form(None),
    personnel_text: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="El archivo del permiso está vacío.")
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El permiso no puede superar 15 MB.")

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename or "permiso")
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    (PERMIT_DIR / stored_name).write_bytes(content)
    text = extract_text(file.filename or "", content)
    image_fields = extract_permit_fields_from_image(file.filename or "", content)
    users = (await session.execute(select(User).where(User.is_active == True))).scalars().all()
    combined_text = f"{text}\n{activity or ''}\n{work_area or ''}\n{personnel_text or ''}"
    personnel = match_personnel(combined_text, users)
    risks = infer_risks(combined_text)
    fields = extract_permit_structured_fields(text, risks)
    permit = SafetyPermit(
        uploaded_by=user.id, filename=file.filename or safe_name,
        source_path=f"/static/security/permits/{stored_name}", extracted_text=text,
        permit_number=extract_permit_number(text) or image_fields["number"],
        activity=activity or fields["activity"],
        work_area=work_area or field_from_text(text, ["área de trabajo", "area de trabajo", "área", "area", "ubicación", "ubicacion"]),
        start_at=field_from_text(text, ["inicio", "fecha de inicio"]),
        end_at=field_from_text(text, ["fin", "fecha de fin"]),
        personnel=", ".join(person.username for person in personnel) or (personnel_text or ""),
        risks=", ".join(risk.name for risk in risks),
    )
    session.add(permit)
    await session.commit()
    await session.refresh(permit)
    result = to_read(permit, personnel, risks)
    result.permit_number = result.permit_number or image_fields["number"]
    result.permit_date = result.permit_date or image_fields["date"]
    result.permit_time = result.permit_time or image_fields["time"]
    result.permit_type = result.permit_type or fields["permit_type"]
    result.equipment = result.equipment or fields["equipment"]
    result.description = result.description or fields["description"]
    result.risk_analysis_number = result.risk_analysis_number or fields["risk_analysis_number"]
    result.work_procedure_number = result.work_procedure_number or fields["work_procedure_number"]
    result.contractor = result.contractor or fields["contractor"]
    result.personnel_count = result.personnel_count or fields["personnel_count"]
    result.valid_until = result.valid_until or fields["valid_until"]
    result.shift = result.shift or fields["shift"]
    result.activity = result.activity or fields["activity"]
    result.signatories = extract_signatories(text) or [person.name or person.username for person in personnel]
    return result


@router.post("/document/analyze", response_model=SafetyDocumentAnalysisRead)
async def analyze_safety_document(
    file: UploadFile = File(...),
    document_type: str = Form(...),
    activity: Optional[str] = Form(None),
    work_area: Optional[str] = Form(None),
    user: User = Depends(current_active_user),
):
    if document_type not in {"art", "vit"}:
        raise HTTPException(status_code=400, detail="El tipo de documento debe ser ART o VIT.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El archivo no puede superar 15 MB.")

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename or document_type)
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    (PERMIT_DIR / stored_name).write_bytes(content)
    text = extract_text(file.filename or "", content)
    combined_text = f"{text}\n{activity or ''}\n{work_area or ''}"
    risks = infer_risks(combined_text)

    return SafetyDocumentAnalysisRead(
        document_type=document_type.upper(),
        filename=file.filename or safe_name,
        source_path=f"/static/security/permits/{stored_name}",
        extracted_text=text,
        activity=activity or infer_activity(combined_text, risks),
        work_area=work_area or field_from_text(text, ["área de trabajo", "area de trabajo", "área", "area", "ubicación", "ubicacion"]),
        risks=risks,
    )


@router.get("/permit/history", response_model=List[PermitAnalysisRead])
async def permit_history(session: AsyncSession = Depends(get_async_session), user: User = Depends(current_active_user)):
    permits = (await session.execute(select(SafetyPermit).order_by(SafetyPermit.created_at.desc()))).scalars().all()
    users = (await session.execute(select(User).where(User.is_active == True))).scalars().all()
    result = []
    for permit in permits:
        result.append(to_read(permit, match_personnel(permit.personnel or "", users), infer_risks(f"{permit.activity or ''} {permit.risks or ''}")))
    return result
