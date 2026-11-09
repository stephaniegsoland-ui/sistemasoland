import base64
import hashlib
import logging
import os
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from PIL import Image
from pydantic import BaseModel
from sqlmodel import Field, SQLModel, select
from sqlalchemy import Column, Text, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.storage import media_dir, read_upload, safe_filename_suffix
from app.core.db import get_async_session
from app.core.vision import classify_image_bytes, get_best_embedding
from app.models.user import User
import io
import numpy as np
try:
    import cv2
except Exception:
    cv2 = None
try:
    import face_recognition
except Exception:
    face_recognition = None

STATIC_SECURITY_DIR = media_dir("security")
logger = logging.getLogger("soland.security.epp")

router = APIRouter()


class SecurityEppReport(SQLModel, table=True):
    __tablename__ = "security_epp_report"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    operator_name: Optional[str] = Field(default=None)
    turno: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    image_path: str
    thumbnail_path: Optional[str] = Field(default=None)
    score: float = Field(default=0.0)
    person_detected: bool = Field(default=False)
    present_items: Optional[str] = Field(default=None)
    missing_items: Optional[str] = Field(default=None)
    summary: str = Field(default="", sa_column=Column(Text, nullable=False))
    recommendations: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=datetime.now)


class SecurityEppReportRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    operator_name: Optional[str] = None
    turno: Optional[str] = None
    notes: Optional[str] = None
    image_path: str
    thumbnail_path: Optional[str] = None
    score: float
    person_detected: bool
    present_items: Optional[str] = None
    missing_items: Optional[str] = None
    summary: str
    recommendations: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SecurityEppAnalysisResult(BaseModel):
    person_detected: bool
    face_detected: bool = False
    person_bbox: Optional[List[int]] = None
    detected_items: Dict[str, bool]
    present_items: List[str]
    missing_items: List[str]
    score: float
    summary: str
    recommendations: List[str]
    full_body_detected: bool = True
    recognized_username: Optional[str] = None
    recognition_precision: Optional[float] = None


EPP_CLASS_ALIASES = {
    "person": {"person", "human", "worker", "persona"},
    "full_body_person": {
        "full body person",
        "full body worker",
        "full body",
        "persona cuerpo completo",
        "persona de cuerpo completo",
        "cuerpo completo",
    },
    "face": {"face", "human face", "rostro", "cara"},
    "cascos": {"helmet", "hard hat", "hardhat", "safety helmet", "casco", "cascos"},
    "chaleco": {"safety vest", "reflective vest", "hi vis vest", "vest", "chaleco"},
    "guantes": {"gloves", "glove", "safety gloves", "guante", "guantes"},
    "lentes de seguridad": {
        "safety glasses",
        "protective glasses",
        "goggles",
        "lentes",
        "lentes de seguridad",
    },
    "tapones de oído": {
        "ear plugs",
        "earplugs",
        "ear protection",
        "hearing protection",
        "tapones de oido",
        "tapones de oído",
    },
    "braga de seguridad": {
        "coveralls",
        "coverall",
        "overalls",
        "safety coveralls",
        "braga de seguridad",
        "braga",
    },
    "botas": {"safety boots", "work boots", "boots", "boot", "botas", "bota"},
}
REQUIRED_EPP_CLASSES = (
    "cascos",
    "chaleco",
    "guantes",
    "lentes de seguridad",
    "tapones de oído",
    "braga de seguridad",
    "botas",
)


def _normalize_model_class(label: str) -> str:
    normalized = unicodedata.normalize("NFKD", label.strip().lower())
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", without_accents).strip()


_NORMALIZED_EPP_ALIASES = {
    _normalize_model_class(alias): class_name
    for class_name, aliases in EPP_CLASS_ALIASES.items()
    for alias in aliases
}


def parse_roboflow_predictions(
    payload: Dict[str, object],
    image_width: int,
    image_height: int,
    confidence_threshold: float,
) -> SecurityEppAnalysisResult:
    raw_predictions = payload.get("predictions")
    if not isinstance(raw_predictions, list):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El modelo de EPP devolvió una respuesta inválida.",
        )

    predictions: List[Dict[str, object]] = []
    for raw_prediction in raw_predictions:
        if not isinstance(raw_prediction, dict):
            continue
        label = raw_prediction.get("class")
        if not isinstance(label, str):
            continue
        try:
            confidence = float(raw_prediction.get("confidence", 0))
            center_x = float(raw_prediction["x"])
            center_y = float(raw_prediction["y"])
            width = float(raw_prediction["width"])
            height = float(raw_prediction["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if confidence < confidence_threshold or width <= 0 or height <= 0:
            continue

        x1 = max(0.0, center_x - width / 2)
        y1 = max(0.0, center_y - height / 2)
        x2 = min(float(image_width), center_x + width / 2)
        y2 = min(float(image_height), center_y + height / 2)
        if x2 <= x1 or y2 <= y1:
            continue

        normalized_label = _normalize_model_class(label)
        class_name = _NORMALIZED_EPP_ALIASES.get(normalized_label)
        if class_name:
            predictions.append({
                "class": class_name,
                "confidence": confidence,
                "bbox": (x1, y1, x2, y2),
            })

    people = [prediction for prediction in predictions if prediction["class"] == "person"]
    full_body_people = [
        prediction for prediction in predictions
        if prediction["class"] == "full_body_person"
    ]
    if len(people) != 1 or len(full_body_people) != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No se pudo confirmar una sola persona con el cuerpo completo. Toma una foto clara, de frente y de cuerpo completo.",
        )

    person_box = people[0]["bbox"]
    full_body_box = full_body_people[0]["bbox"]
    if not (
        full_body_box[0] <= person_box[0]
        and full_body_box[1] <= person_box[1]
        and full_body_box[2] >= person_box[2]
        and full_body_box[3] >= person_box[3]
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El modelo no pudo confirmar que el cuerpo completo esté visible. Toma otra foto.",
        )

    faces = [prediction for prediction in predictions if prediction["class"] == "face"]
    face_in_person = any(
        person_box[0] <= (face["bbox"][0] + face["bbox"][2]) / 2 <= person_box[2]
        and person_box[1] <= (face["bbox"][1] + face["bbox"][3]) / 2 <= person_box[3]
        for face in faces
    )
    if not face_in_person:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No se detectó claramente el rostro de la persona. Toma una foto de frente y bien iluminada.",
        )

    detected_items = {item: False for item in REQUIRED_EPP_CLASSES}
    for prediction in predictions:
        class_name = prediction["class"]
        if class_name not in detected_items:
            continue
        item_box = prediction["bbox"]
        item_center_x = (item_box[0] + item_box[2]) / 2
        item_center_y = (item_box[1] + item_box[3]) / 2
        if (
            person_box[0] <= item_center_x <= person_box[2]
            and person_box[1] <= item_center_y <= person_box[3]
        ):
            detected_items[class_name] = True

    present_items = [item for item in REQUIRED_EPP_CLASSES if detected_items[item]]
    missing_items = [item for item in REQUIRED_EPP_CLASSES if not detected_items[item]]
    score = round(100 * len(present_items) / len(REQUIRED_EPP_CLASSES), 1)
    recommendations = [
        f"Usar {item} antes de ingresar."
        for item in missing_items
    ]
    summary = (
        "Se detectó una persona con cuerpo completo y rostro visible. "
        + (
            f"Faltan EPP: {', '.join(missing_items)}."
            if missing_items
            else "Se detectaron todos los EPP requeridos."
        )
    )

    return SecurityEppAnalysisResult(
        person_detected=True,
        face_detected=True,
        person_bbox=[round(value) for value in person_box],
        detected_items=detected_items,
        present_items=present_items,
        missing_items=missing_items,
        score=score,
        summary=summary,
        recommendations=recommendations,
        full_body_detected=True,
    )


async def analyze_with_roboflow(file_bytes: bytes, image_width: int, image_height: int) -> SecurityEppAnalysisResult:
    api_key = os.getenv("ROBOFLOW_API_KEY")
    model_id = os.getenv("ROBOFLOW_MODEL_ID", "").strip()
    if not api_key or not model_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El detector Roboflow aún no está configurado. Configura ROBOFLOW_API_KEY y ROBOFLOW_MODEL_ID.",
        )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", model_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ROBOFLOW_MODEL_ID debe tener el formato proyecto/version.",
        )

    try:
        confidence_threshold = float(os.getenv("ROBOFLOW_CONFIDENCE_THRESHOLD", "0.5"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ROBOFLOW_CONFIDENCE_THRESHOLD debe ser un valor entre 0 y 1.",
        ) from exc
    if not 0 <= confidence_threshold <= 1:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ROBOFLOW_CONFIDENCE_THRESHOLD debe ser un valor entre 0 y 1.",
        )

    image_base64 = base64.b64encode(file_bytes).decode("ascii")
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                f"https://serverless.roboflow.com/{model_id}",
                content=image_base64,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("Roboflow EPP inference failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo completar el análisis con el modelo Roboflow.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El modelo Roboflow devolvió una respuesta inválida.",
        )
    return parse_roboflow_predictions(
        payload,
        image_width,
        image_height,
        confidence_threshold,
    )


def decode_photo_data(photo_data: str) -> bytes:
    if photo_data.startswith("data:"):
        try:
            _, encoded = photo_data.split(",", 1)
        except ValueError:
            encoded = photo_data
    else:
        encoded = photo_data
    return base64.b64decode(encoded)


def compute_image_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


async def recognize_user_by_image(file_bytes: bytes, session: AsyncSession) -> Tuple[Optional[str], Optional[float]]:
    # Try face recognition when available, then LBPH (OpenCV) fallback, then exact hash match.
    # Prefer stored photo_data, but also allow stored photo_path images for legacy users.
    users_query = select(User).where(or_(User.photo_data != None, User.photo_path != None))
    result = await session.execute(users_query)
    users = result.scalars().all()

    def get_user_image_bytes(user: User) -> Optional[bytes]:
        if user.photo_data:
            try:
                return decode_photo_data(user.photo_data)
            except Exception:
                return None
        if user.photo_path:
            try:
                path = Path(__file__).resolve().parent.parent.parent / user.photo_path.lstrip("/")
                return path.read_bytes()
            except Exception:
                return None
        return None

    def _detect_face_crop(image_bytes: bytes) -> Optional[Image.Image]:
        if cv2 is None:
            return None
        try:
            arr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return None
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            face_cascade = cv2.CascadeClassifier(cascade_path)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
            if len(faces) == 0:
                return None
            x, y, w, h = faces[0]
            roi = img[y : y + h, x : x + w]
            roi = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
            return Image.fromarray(roi)
        except Exception:
            return None

    def _get_image_embedding_from_bytes(image_bytes: bytes):
        try:
            face_crop = _detect_face_crop(image_bytes)
            if face_crop is not None:
                embedding = get_best_embedding(face_crop)
                if embedding is not None:
                    return embedding
            return get_best_embedding(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
        except Exception:
            return None

    # First try face_recognition for robust match
    if face_recognition is not None:
        try:
            img = face_recognition.load_image_file(io.BytesIO(file_bytes))
            encodings = face_recognition.face_encodings(img)
            if len(encodings) > 0:
                target_encoding = encodings[0]
                for user in users:
                    user_bytes = get_user_image_bytes(user)
                    if not user_bytes:
                        continue
                    try:
                        user_img = face_recognition.load_image_file(io.BytesIO(user_bytes))
                        user_encs = face_recognition.face_encodings(user_img)
                        if len(user_encs) == 0:
                            continue
                        dist = face_recognition.face_distance([user_encs[0]], target_encoding)[0]
                        if dist < 0.6:
                            precision = max(0.0, 1.0 - dist)
                            return user.username, precision
                    except Exception:
                        continue
        except Exception:
            pass

    # Next fallback: LBPH recognizer using stored photos (requires cv2.face)
    if cv2 is not None:
        try:
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            face_cascade = cv2.CascadeClassifier(cascade_path)

            def detect_face_region(image_bytes: bytes):
                try:
                    arr = np.frombuffer(image_bytes, np.uint8)
                    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if img is None:
                        return None
                    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
                    if len(faces) == 0:
                        return None
                    x, y, w, h = faces[0]
                    face = gray[y : y + h, x : x + w]
                    return cv2.resize(face, (120, 120))
                except Exception:
                    return None

            if hasattr(cv2, "face"):
                training_images = []
                labels = []
                label_map = {}
                next_label = 0
                for user in users:
                    user_bytes = get_user_image_bytes(user)
                    if not user_bytes:
                        continue
                    user_face = detect_face_region(user_bytes)
                    if user_face is None:
                        continue
                    training_images.append(user_face)
                    labels.append(next_label)
                    label_map[next_label] = user.username
                    next_label += 1

                if len(training_images) > 0:
                    try:
                        recognizer = cv2.face.LBPHFaceRecognizer_create()
                        recognizer.train(training_images, np.array(labels))
                        target_face = detect_face_region(file_bytes)
                        if target_face is not None:
                            label, conf = recognizer.predict(target_face)
                            precision = max(0.0, 1.0 - (conf / 300.0))
                            if conf < 100 and label in label_map:
                                return label_map[label], precision
                    except Exception:
                        pass

            # If cv2.face is unavailable, use a simple face-region similarity fallback
            target_face = detect_face_region(file_bytes)
            if target_face is not None:
                for user in users:
                    user_bytes = get_user_image_bytes(user)
                    if not user_bytes:
                        continue
                    user_face = detect_face_region(user_bytes)
                    if user_face is None:
                        continue
                    try:
                        diff = np.mean(np.abs(target_face.astype('int16') - user_face.astype('int16')))
                        if diff < 32:
                            precision = max(0.0, 1.0 - diff / 100.0)
                            return user.username, precision
                    except Exception:
                        continue
        except Exception:
            pass

    # Next fallback: image embedding similarity on stored user photos
    try:
        target_embedding = get_best_embedding(Image.open(io.BytesIO(file_bytes)).convert("RGB"))
        if target_embedding is not None:
            best_label = None
            best_sim = 0.0
            for user in users:
                user_bytes = get_user_image_bytes(user)
                if not user_bytes:
                    continue
                try:
                    user_img = Image.open(io.BytesIO(user_bytes)).convert("RGB")
                    user_embedding = get_best_embedding(user_img)
                    if user_embedding is None:
                        continue
                    # cosine similarity
                    sim = float((target_embedding @ user_embedding.T).item())
                    if sim > best_sim:
                        best_sim = sim
                        best_label = user.username
                except Exception:
                    continue
            if best_label and best_sim > 0.35:
                return best_label, float(best_sim)
    except Exception:
        pass

    # Final fallback: exact hash match
    source_hash = compute_image_hash(file_bytes)
    for user in users:
        user_bytes = get_user_image_bytes(user)
        if not user_bytes:
            continue
        try:
            if compute_image_hash(user_bytes) == source_hash:
                return user.username, 1.0
        except Exception:
            continue

    return None, None


def build_analysis_summary(
    analysis: SecurityEppAnalysisResult,
    recognized_username: Optional[str],
    recognition_precision: Optional[float],
    operator_name: Optional[str],
) -> str:
    missing_items = analysis.missing_items or []
    present_items = analysis.present_items or []
    if recognized_username:
        if not operator_name:
            prefix = f"Operador identificado como {recognized_username}."
        else:
            prefix = f"Operador identificado como {recognized_username} (ingresado: {operator_name})."
    else:
        prefix = "No se encontró coincidencia con un operador registrado en la foto."

    if missing_items:
        missing_text = ", ".join(missing_items)
        detail = f"Faltan EPP: {missing_text}."
    else:
        detail = "No faltan EPP detectados en la imagen."

    if recognized_username and recognition_precision is not None:
        detail += f" Precisión de reconocimiento: {int(recognition_precision * 100)}%."

    return f"{prefix} {detail}".strip()


def analyze_image(file_bytes: bytes) -> SecurityEppAnalysisResult:
    detected_items = {
        "cascos": False,
        "chaleco": False,
        "guantes": False,
        "lentes de seguridad": False,
        "tapones de oído": False,
        "braga de seguridad": False,
        "botas": False,
    }
    person_bbox = None
    full_body_detected = False

    def color_mask(rgb, lower, upper):
        return np.logical_and.reduce((rgb[:, :, 0] >= lower[0], rgb[:, :, 0] <= upper[0],
                                      rgb[:, :, 1] >= lower[1], rgb[:, :, 1] <= upper[1],
                                      rgb[:, :, 2] >= lower[2], rgb[:, :, 2] <= upper[2]))

    def count_ratio(region, lower, upper):
        mask = color_mask(region, lower, upper)
        return float(mask.sum()) / max(1, region.shape[0] * region.shape[1])

    def predict_items_with_clip():
        try:
            classification = classify_image_bytes(file_bytes, labels=[
                "casco de seguridad",
                "chaleco reflectante",
                "lentes de seguridad",
                "guantes de seguridad",
                "tapones de oído",
            ])
            if isinstance(classification, dict):
                label = classification.get("label", "")
                if label == "casco de seguridad":
                    detected_items["cascos"] = True
                elif label == "chaleco reflectante":
                    detected_items["chaleco"] = True
                elif label == "lentes de seguridad":
                    detected_items["lentes de seguridad"] = True
                elif label == "guantes de seguridad":
                    detected_items["guantes"] = True
                elif label == "tapones de oído":
                    detected_items["tapones de oído"] = True
        except Exception:
            pass

    if cv2 is None:
        predict_items_with_clip()

    try:
        pil_img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        rgb = np.array(pil_img)
        h, w = rgb.shape[:2]

        # person detection fallback: if face-like or vest-like colors exist
        top_region = rgb[0 : max(1, int(h * 0.25)), :]
        mid_region = rgb[int(h * 0.2) : int(h * 0.7), :]
        lower_region = rgb[int(h * 0.6) :, :]

        # detect vest by yellow/orange area in torso region
        vest_ratio = max(
            count_ratio(mid_region, (180, 120, 0), (255, 220, 120)),
            count_ratio(mid_region, (180, 100, 0), (255, 200, 120)),
        )
        if vest_ratio > 0.02:
            detected_items["chaleco"] = True

        # detect helmet by bright white or orange/yellow in top
        helmet_ratio = max(
            count_ratio(top_region, (200, 200, 200), (255, 255, 255)),
            count_ratio(top_region, (180, 120, 0), (255, 230, 120)),
        )
        if helmet_ratio > 0.01:
            detected_items["cascos"] = True

        # detect glasses by dark band in upper-middle region
        face_region = rgb[int(h * 0.12) : int(h * 0.32), int(w * 0.2) : int(w * 0.8)]
        if face_region.size != 0:
            dark_ratio = float((face_region < 70).all(axis=2).sum()) / max(1, face_region.shape[0] * face_region.shape[1])
            if dark_ratio > 0.06:
                detected_items["lentes de seguridad"] = True

        # detect gloves by bright or saturated pixels in lower region
        if lower_region.size != 0:
            yellow_lower_ratio = count_ratio(lower_region, (180, 120, 0), (255, 230, 120))
            orange_lower_ratio = count_ratio(lower_region, (180, 100, 0), (255, 200, 120))
            if max(yellow_lower_ratio, orange_lower_ratio) > 0.01:
                detected_items["guantes"] = True

        # detect braga by reflective / high-saturation lower-mid colors in torso/lower body
        if mid_region.size != 0:
            braga_ratio = max(
                count_ratio(mid_region, (5, 80, 80), (25, 255, 255)),
                count_ratio(mid_region, (180, 120, 0), (255, 230, 120)),
            )
            if braga_ratio > 0.02:
                detected_items["braga de seguridad"] = True

        # detect boots by dark/solid footwear-like region in the bottom area
        if lower_region.size != 0:
            boots_dark_ratio = float((lower_region < 70).all(axis=2).sum()) / max(1, lower_region.shape[0] * lower_region.shape[1])
            if boots_dark_ratio > 0.08:
                detected_items["botas"] = True

        # ear protection fallback: dark or solid colours near head sides
        if face_region.size != 0:
            left_ear = face_region[:, : max(1, int(face_region.shape[1] * 0.2)), :]
            right_ear = face_region[:, -max(1, int(face_region.shape[1] * 0.2)) :, :]
            for ear_roi in (left_ear, right_ear):
                if ear_roi.size == 0:
                    continue
                dark_ratio = float((ear_roi < 90).all(axis=2).sum()) / max(1, ear_roi.shape[0] * ear_roi.shape[1])
                if dark_ratio > 0.15:
                    detected_items["tapones de oído"] = True
                    break

        # person bbox fallback when image has a likely operator
        if vest_ratio > 0.01 or helmet_ratio > 0.005 or detected_items["lentes de seguridad"]:
            person_bbox = [0, 0, w, h]
    except Exception:
        pass

    if cv2 is not None:
        try:
            arr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                hog = cv2.HOGDescriptor()
                hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
                rects, weights = hog.detectMultiScale(gray, winStride=(8, 8), padding=(8, 8), scale=1.05)
                if len(rects) > 0:
                    areas = [w * h for (x, y, w, h) in rects]
                    idx = int(np.argmax(areas))
                    x, y, w, h = rects[idx]
                    person_bbox = [int(x), int(y), int(x + w), int(y + h)]
                else:
                    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                    face_cascade = cv2.CascadeClassifier(cascade_path)
                    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
                    if len(faces) > 0:
                        x, y, w, h = faces[0]
                        person_bbox = [int(x), int(y), int(x + w), int(y + h)]
                    else:
                        h_img, w_img = img.shape[:2]
                        person_bbox = [0, 0, w_img, h_img]

                if person_bbox is not None:
                    x1, y1, x2, y2 = person_bbox
                    person_roi = img[y1:y2, x1:x2]
                    hsv = cv2.cvtColor(person_roi, cv2.COLOR_BGR2HSV)

                    lower_yellow = np.array([15, 80, 80])
                    upper_yellow = np.array([35, 255, 255])
                    mask_yellow = cv2.inRange(hsv, lower_yellow, upper_yellow)
                    yellow_ratio = mask_yellow.sum() / (255.0 * max(1, person_roi.shape[0] * person_roi.shape[1]))
                    if yellow_ratio > 0.02:
                        detected_items["chaleco"] = True

                    lower_orange = np.array([5, 80, 80])
                    upper_orange = np.array([15, 255, 255])
                    mask_orange = cv2.inRange(hsv, lower_orange, upper_orange)
                    orange_ratio = mask_orange.sum() / (255.0 * max(1, person_roi.shape[0] * person_roi.shape[1]))
                    if orange_ratio > 0.02:
                        detected_items["chaleco"] = True

                    head_h = max(10, int(person_roi.shape[0] * 0.25))
                    head_roi = person_roi[0:head_h, :]
                    hsv_head = cv2.cvtColor(head_roi, cv2.COLOR_BGR2HSV)
                    lower_white = np.array([0, 0, 200])
                    upper_white = np.array([180, 40, 255])
                    mask_white = cv2.inRange(hsv_head, lower_white, upper_white)
                    white_ratio = mask_white.sum() / (255.0 * max(1, head_roi.shape[0] * head_roi.shape[1]))
                    if white_ratio > 0.01:
                        detected_items["cascos"] = True
                    mask_helmet_yellow = cv2.inRange(hsv_head, lower_yellow, upper_yellow)
                    mask_helmet_orange = cv2.inRange(hsv_head, lower_orange, upper_orange)
                    if (mask_helmet_yellow.sum() + mask_helmet_orange.sum()) / (255.0 * max(1, head_roi.shape[0] * head_roi.shape[1])) > 0.008:
                        detected_items["cascos"] = True

                    face_roi = person_roi[int(person_roi.shape[0] * 0.15) : int(person_roi.shape[0] * 0.45), :]
                    if face_roi.size != 0:
                        gray_face = cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)
                        dark_ratio = (gray_face < 60).sum() / (gray_face.size + 1e-9)
                        if dark_ratio > 0.06:
                            detected_items["lentes de seguridad"] = True

                    hands_roi = person_roi[int(person_roi.shape[0] * 0.55) : person_roi.shape[0], :]
                    if hands_roi.size != 0:
                        hsv_hands = cv2.cvtColor(hands_roi, cv2.COLOR_BGR2HSV)
                        s = hsv_hands[:, :, 1]
                        sat_ratio = (s > 80).sum() / (s.size + 1e-9)
                        if sat_ratio > 0.02:
                            detected_items["guantes"] = True

                    ear_y = int(person_roi.shape[0] * 0.15)
                    ear_h = int(person_roi.shape[0] * 0.4)
                    left_ear_roi = person_roi[ear_y : ear_y + ear_h, 0 : int(person_roi.shape[1] * 0.25)]
                    right_ear_roi = person_roi[ear_y : ear_y + ear_h, int(person_roi.shape[1] * 0.75) : person_roi.shape[1]]
                    for ear_roi in (left_ear_roi, right_ear_roi):
                        if ear_roi.size == 0:
                            continue
                        hsv_ear = cv2.cvtColor(ear_roi, cv2.COLOR_BGR2HSV)
                        v = hsv_ear[:, :, 2]
                        s = hsv_ear[:, :, 1]
                        dark_ratio = (v < 90).sum() / (v.size + 1e-9)
                        saturated_ratio = (s > 80).sum() / (s.size + 1e-9)
                        if dark_ratio > 0.18 or saturated_ratio > 0.18:
                            detected_items["tapones de oído"] = True
                            break
        except Exception:
            pass

    if person_bbox is None:
        person_bbox = [0, 0, 1, 1]

    present_items = [item for item, has in detected_items.items() if has]
    missing_items = [item for item, has in detected_items.items() if not has]
    score = max(0.0, min(100.0, 100.0 - len(missing_items) * 20.0))
    summary = (
        f"El análisis indica que se detectaron {len(present_items)} elementos de EPP."
    )
    if len(missing_items) == 0:
        summary = f"{summary} El operador lleva el EPP requerido para entrar al área de trabajo."
    else:
        summary = f"{summary} Faltan EPP: {', '.join(missing_items)}."
    recommendations = []
    if "chaleco" not in present_items:
        recommendations.append("Usar chaleco reflectante antes de ingresar.")
    if "guantes" not in present_items:
        recommendations.append("Colocar guantes de seguridad para manipular materiales.")
    if "lentes de seguridad" not in present_items:
        recommendations.append("Usar lentes de seguridad para proteger la vista.")
    if "tapones de oído" not in present_items:
        recommendations.append("Colocar protección auditiva antes de entrar a áreas ruidosas.")
    if len(missing_items) == 0:
        recommendations.append("Puede ingresar al área, pero mantiene una postura segura.")

    return SecurityEppAnalysisResult(
        person_detected=bool(person_bbox),
        person_bbox=person_bbox,
        detected_items=detected_items,
        present_items=present_items,
        missing_items=missing_items,
        score=score,
        summary=summary,
        recommendations=recommendations,
        recognized_username=None,
        recognition_precision=None,
    )


@router.post("/epp/analyze", response_model=SecurityEppAnalysisResult)
async def analyze_epp_image(
    file: UploadFile = File(...),
    operator_name: Optional[str] = Form(None),
    turno: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        file_bytes = await read_upload(
            file,
            allowed_extensions={".png", ".jpg", ".jpeg", ".webp", ".bmp"},
            allowed_content_types={"image/png", "image/jpeg", "image/webp", "image/bmp"},
        )
        image = Image.open(io.BytesIO(file_bytes))
        image_width, image_height = image.size
        image.verify()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Archivo de imagen no válido.") from exc

    analysis = await analyze_with_roboflow(file_bytes, image_width, image_height)
    image_name = f"{uuid.uuid4().hex}{safe_filename_suffix(file.filename, '.jpg')}"
    target_path = STATIC_SECURITY_DIR / image_name
    target_path.write_bytes(file_bytes)
    thumbnail_name = f"{uuid.uuid4().hex}_thumb.jpg"
    thumbnail_path = STATIC_SECURITY_DIR / thumbnail_name
    try:
        thumbnail = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        thumbnail.thumbnail((480, 480))
        thumbnail.save(thumbnail_path, format="JPEG", quality=82, optimize=True)
    except Exception:
        thumbnail_path = None

    report = SecurityEppReport(
        user_id=user.id,
        operator_name=operator_name,
        turno=turno,
        notes=notes,
        image_path=f"/static/security/{image_name}",
        thumbnail_path=f"/static/security/{thumbnail_name}" if thumbnail_path else None,
        score=analysis.score,
        person_detected=analysis.person_detected,
        present_items=", ".join(analysis.present_items),
        missing_items=", ".join(analysis.missing_items),
        summary=analysis.summary,
        recommendations=", ".join(analysis.recommendations),
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return analysis


@router.get("/epp/history", response_model=List[SecurityEppReportRead])
async def get_epp_history(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    query = select(SecurityEppReport).where(SecurityEppReport.user_id == user.id).order_by(SecurityEppReport.created_at.desc())
    result = await session.execute(query)
    return result.scalars().all()
