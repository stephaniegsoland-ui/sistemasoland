import uuid
from datetime import datetime
from io import BytesIO
from itertools import product
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from PIL import Image, ImageChops, ImageFilter, ImageDraw as PILImageDraw
import numpy as np
import torch
import torchvision.transforms.functional as TF
from torchvision.models.detection import MaskRCNN_ResNet50_FPN_Weights, maskrcnn_resnet50_fpn
from sqlmodel import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_session
from app.core.vision import classify_image_bytes
from app.models.vehicle import Vehicle, FleetRecord, TypeRecord, VehicleInspection
from app.schemas.vehicle import (
    VehicleCreate,
    VehicleRead,
    FleetRecordCreate,
    FleetRecordRead,
    VehicleInspectionRead,
)
from app.core.auth import current_active_user, get_supervisor_or_admin
from app.models.user import User
import os
import asyncio
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

STATIC_INSPECTIONS_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "inspections"
STATIC_INSPECTIONS_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()


def make_mask_from_array(arr, blur_radius=0.8, thresh=18):
    img = Image.fromarray(arr).filter(ImageFilter.GaussianBlur(blur_radius)).convert("L")
    img = img.point(lambda p: 255 if p > thresh else 0)
    img = img.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3)).convert("L")
    return img

_vehicle_segmentation_model = None
_VEHICLE_LABELS = {3, 4, 6, 8}  # COCO: car, motorcycle, bus, truck
_VEHICLE_CHECK_LABELS = [
    "vehicle",
    "car",
    "truck",
    "background",
    "road",
    "ground",
    "dirt",
    "rock",
    "grass",
    "gravel",
    "pavement",
]


def is_vehicle_like_region(image: Image.Image, min_confidence: float = 0.30) -> bool:
    bio = BytesIO()
    image.save(bio, format="PNG")
    result = classify_image_bytes(bio.getvalue(), labels=_VEHICLE_CHECK_LABELS)
    label = result.get("label")
    confidence = float(result.get("confidence") or 0.0)
    engine = result.get("engine")

    if engine != "clip":
        # If CLIP is not available, don't reject regions aggressively based on the vehicle/background label set.
        return True

    if label in {"vehicle", "car", "truck"} and confidence >= 0.20:
        return True
    if label in {"background", "road", "ground", "dirt", "rock", "grass", "gravel", "pavement"} and confidence >= 0.35:
        return False
    return confidence >= min_confidence


def extract_connected_regions(mask_image: Image.Image, min_pixels: int = 20):
    arr = np.array(mask_image.convert("L"))
    height, width = arr.shape
    visited = np.zeros((height, width), dtype=bool)
    regions = []

    for y in range(height):
        for x in range(width):
            if arr[y, x] > 0 and not visited[y, x]:
                stack = [(x, y)]
                visited[y, x] = True
                min_x = max_x = x
                min_y = max_y = y
                pixel_count = 0

                while stack:
                    cx, cy = stack.pop()
                    pixel_count += 1
                    min_x = min(min_x, cx)
                    max_x = max(max_x, cx)
                    min_y = min(min_y, cy)
                    max_y = max(max_y, cy)

                    for nx, ny in (
                        (cx + 1, cy),
                        (cx - 1, cy),
                        (cx, cy + 1),
                        (cx, cy - 1),
                        (cx + 1, cy + 1),
                        (cx + 1, cy - 1),
                        (cx - 1, cy + 1),
                        (cx - 1, cy - 1),
                    ):
                        if 0 <= nx < width and 0 <= ny < height and not visited[ny, nx] and arr[ny, nx] > 0:
                            visited[ny, nx] = True
                            stack.append((nx, ny))

                if pixel_count >= min_pixels:
                    regions.append({
                        "bbox": (min_x, min_y, max_x + 1, max_y + 1),
                        "pixel_count": pixel_count,
                    })
    return regions


def split_large_region(region_bbox, diff_gray):
    x0, y0, x1, y1 = region_bbox
    crop = diff_gray.crop((x0, y0, x1, y1)).convert("L")
    if crop.width < 24 or crop.height < 24:
        return [{"bbox": region_bbox, "pixel_count": sum(1 for px in crop.getdata() if px > 0)}]

    def split_by_projection(mask_arr, axis: str):
        if axis == "vertical":
            projection = mask_arr.sum(axis=0)
            length = mask_arr.shape[1]
        else:
            projection = mask_arr.sum(axis=1)
            length = mask_arr.shape[0]

        max_val = projection.max() if projection.size else 0
        threshold = max(2, int(max_val * 0.12))
        segments = []
        start = None

        for idx, value in enumerate(projection.tolist()):
            if value > threshold:
                if start is None:
                    start = idx
            elif start is not None:
                if idx - start > max(8, length // 20):
                    segments.append((start, idx))
                start = None
        if start is not None and length - start > max(8, length // 20):
            segments.append((start, length))

        if len(segments) <= 1:
            return None

        found = []
        for start, end in segments:
            if axis == "vertical":
                found.append((start, 0, end, mask_arr.shape[0]))
            else:
                found.append((0, start, mask_arr.shape[1], end))
        return found

    crop_arr = np.array(crop)
    mask = (crop_arr > 0).astype(np.uint8)
    smooth = make_mask_from_array(crop_arr, blur_radius=0.4, thresh=22)
    smoothed_arr = (np.array(smooth) > 0).astype(np.uint8)

    split_candidates = []
    if crop.width >= crop.height:
        split_candidates = split_by_projection(smoothed_arr, "vertical") or []
    if not split_candidates:
        split_candidates = split_by_projection(smoothed_arr, "horizontal") or []

    if split_candidates and len(split_candidates) > 1:
        merged = []
        for seg in split_candidates:
            sx0, sy0, sx1, sy1 = seg
            if sx1 - sx0 > 8 and sy1 - sy0 > 8:
                merged.append({
                    "bbox": (x0 + sx0, y0 + sy0, x0 + sx1, y0 + sy1),
                    "pixel_count": int(mask[sy0:sy1, sx0:sx1].sum()),
                })
        if len(merged) > 1:
            return merged

    def try_split(thresh: int, min_pixels: int):
        mask_img = make_mask_from_array(np.array(crop), blur_radius=0.35, thresh=thresh)
        mask_img = mask_img.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
        return extract_connected_regions(mask_img, min_pixels=min_pixels)

    thresholds = [(28, 14), (34, 12), (40, 10), (48, 8)]
    for thresh, min_pixels in thresholds:
        parts = try_split(thresh, min_pixels)
        if len(parts) > 1:
            total_pixels = sum(part["pixel_count"] for part in parts)
            if total_pixels >= 0.08 * (crop.width * crop.height):
                split_regions = []
                for part in parts:
                    px0, py0, px1, py1 = part["bbox"]
                    split_regions.append({
                        "bbox": (x0 + px0, y0 + py0, x0 + px1, y0 + py1),
                        "pixel_count": part["pixel_count"],
                    })
                return split_regions

    mask_img = make_mask_from_array(np.array(crop), blur_radius=0.35, thresh=22)
    mask_img = mask_img.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    parts = extract_connected_regions(mask_img, min_pixels=20)
    if len(parts) <= 1:
        return [{"bbox": region_bbox, "pixel_count": sum(1 for px in crop.getdata() if px > 0)}]
    total_pixels = sum(part["pixel_count"] for part in parts)
    if total_pixels < 0.20 * ((x1 - x0) * (y1 - y0)):
        return [{"bbox": region_bbox, "pixel_count": sum(1 for px in crop.getdata() if px > 0)}]

    split_regions = []
    for part in parts:
        px0, py0, px1, py1 = part["bbox"]
        split_regions.append({
            "bbox": (x0 + px0, y0 + py0, x0 + px1, y0 + py1),
            "pixel_count": part["pixel_count"],
        })
    return split_regions


def get_vehicle_segmentation_model():
    global _vehicle_segmentation_model
    if _vehicle_segmentation_model is not None:
        return _vehicle_segmentation_model
    try:
        weights = MaskRCNN_ResNet50_FPN_Weights.DEFAULT
        model = maskrcnn_resnet50_fpn(weights=weights)
        model = model.to("cpu")
        model.eval()
        _vehicle_segmentation_model = model
    except Exception:
        _vehicle_segmentation_model = None
    return _vehicle_segmentation_model


def detect_vehicle_mask(image: Image.Image, threshold: float = 0.35):
    model = get_vehicle_segmentation_model()
    if model is None:
        return None
    try:
        img_tensor = TF.to_tensor(image).to("cpu")
        with torch.no_grad():
            outputs = model([img_tensor])[0]
        labels = outputs["labels"].cpu().numpy()
        scores = outputs["scores"].cpu().numpy()
        masks = outputs["masks"].cpu()
        selected_mask = None
        for label, score, mask in zip(labels, scores, masks):
            if int(label) not in _VEHICLE_LABELS or score < threshold:
                continue
            mask_binary = (mask[0] > 0.5).to(torch.uint8)
            if selected_mask is None:
                selected_mask = mask_binary
            else:
                selected_mask = selected_mask | mask_binary
        if selected_mask is None:
            return None
        arr = selected_mask.cpu().numpy().astype(np.uint8) * 255
        return Image.fromarray(arr, mode="L")
    except Exception:
        return None


def estimate_vehicle_mask(image: Image.Image):
    mask = detect_vehicle_mask(image)
    if mask is not None:
        return mask
    # Combine edge + local variance to create candidate mask
    gray = image.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_arr = np.array(edges)
    edge_mask = (edge_arr > 55).astype(np.uint8) * 255

    arr_gray = np.array(gray)
    blurred = np.array(Image.fromarray(arr_gray).filter(ImageFilter.GaussianBlur(2)))
    std_arr = np.abs(arr_gray - blurred).astype(np.uint8)
    var_mask = (std_arr > 12).astype(np.uint8) * 255

    combined_arr = ((edge_mask > 0) | (var_mask > 0)).astype(np.uint8) * 255
    candidate = make_mask_from_array(combined_arr, blur_radius=1.2, thresh=40)
    candidate = candidate.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(2)).point(lambda p: 255 if p > 30 else 0)

    arr = np.array(candidate)
    height, width = arr.shape

    def classify_component_region(image: Image.Image, bbox):
        x0, y0, x1, y1 = bbox
        pad = 10
        x0 = max(0, x0 - pad)
        y0 = max(0, y0 - pad)
        x1 = min(width, x1 + pad)
        y1 = min(height, y1 + pad)
        crop = image.crop((x0, y0, x1, y1)).convert("RGB")
        bio = BytesIO()
        crop.save(bio, format="PNG")
        vehicle_check_labels = [
            "vehicle",
            "car",
            "truck",
            "background",
            "road",
            "ground",
            "dirt",
            "rock",
            "grass",
            "gravel",
            "pavement",
        ]
        cls = classify_image_bytes(bio.getvalue(), labels=vehicle_check_labels)
        if cls.get("engine") != "clip":
            return 0.0
        label = cls.get("label")
        confidence = float(cls.get("confidence") or 0.0)
        if label in {"vehicle", "car", "truck"}:
            return confidence
        if label in {"background", "road", "ground", "dirt", "rock", "grass", "gravel", "pavement"}:
            return -confidence
        return 0.0

    visited = np.zeros_like(arr, dtype=bool)
    components = []
    center_x = width / 2

    for y in range(height):
        for x in range(width):
            if arr[y, x] == 255 and not visited[y, x]:
                stack = [(x, y)]
                visited[y, x] = True
                min_x = max_x = x
                min_y = max_y = y
                pixel_count = 0
                sum_x = 0
                sum_y = 0
                pixels = []
                while stack:
                    cx, cy = stack.pop()
                    pixel_count += 1
                    sum_x += cx
                    sum_y += cy
                    pixels.append((cx, cy))
                    min_x = min(min_x, cx)
                    max_x = max(max_x, cx)
                    min_y = min(min_y, cy)
                    max_y = max(max_y, cy)
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if 0 <= nx < width and 0 <= ny < height and not visited[ny, nx] and arr[ny, nx] == 255:
                            visited[ny, nx] = True
                            stack.append((nx, ny))
                centroid_x = sum_x / pixel_count
                centroid_y = sum_y / pixel_count
                bbox_area = (max_x - min_x + 1) * (max_y - min_y + 1)
                components.append((pixel_count, min_x, min_y, max_x + 1, max_y + 1, centroid_x, centroid_y, bbox_area, pixels))

    if not components:
        return None

    best = None
    best_score = -1
    for pixel_count, min_x, min_y, max_x, max_y, centroid_x, centroid_y, bbox_area, comp_pixels in components:
        area_frac = bbox_area / (width * height)
        w = max_x - min_x
        h = max_y - min_y
        if w <= 0 or h <= 0:
            continue
        aspect = w / h
        # relax filters to include more plausible vehicle regions and avoid overly tight mask selection
        if pixel_count < 250 or area_frac < 0.01 or area_frac > 0.9:
            continue
        if aspect < 0.8 or aspect > 8.0:
            continue
        center_dist_x = abs(centroid_x - center_x) / (width / 2)
        center_score = max(0.0, 1.0 - center_dist_x)
        size_score = min(1.0, pixel_count / (width * height * 0.4))
        aspect_score = 1.0 - abs(aspect - 2.5) / 3.5

        # use CLIP to check if region is likely vehicle and not background
        region_bbox = (min_x, min_y, max_x, max_y)
        ml_score = classify_component_region(image, region_bbox)
        if ml_score < 0:
            continue

        score = size_score * 0.4 + max(aspect_score, 0) * 0.2 + center_score * 0.2 + ml_score * 0.2
        if score > best_score:
            best_score = score
            best = (min_x, min_y, max_x, max_y, comp_pixels)

    if best is None:
        # fallback to a broader candidate mask instead of dropping vehicle mask entirely
        candidate_arr = np.array(candidate)
        if candidate_arr.sum() > 0:
            return candidate
        return None

    min_x, min_y, max_x, max_y, best_pixels = best

    # create a precise mask from the selected component pixels
    mask_arr = np.zeros((height, width), dtype=np.uint8)
    for px, py in best_pixels:
        if 0 <= py < height and 0 <= px < width:
            mask_arr[py, px] = 255

    mask = Image.fromarray(mask_arr, mode="L")
    # slightly dilate and smooth to include edges and avoid jagged artifact
    mask = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(1.2)).point(lambda p: 255 if p > 30 else 0)
    return mask


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


@router.post(
    "/inspection/{inspection_id}/generate_pdf",
    response_model=VehicleInspectionRead,
)
async def generate_inspection_pdf(
    inspection_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    inspection = await session.get(VehicleInspection, inspection_id)
    if not inspection:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspección no encontrada.")

    if not (user.is_superuser or user.level in (1, 2)) and inspection.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso denegado.")

    pdf_filename = f"{inspection.id}.pdf"
    pdf_path = STATIC_INSPECTIONS_DIR / pdf_filename

    def build_pdf():
        c = canvas.Canvas(str(pdf_path), pagesize=A4)
        width, height = A4
        margin = 40
        x = margin
        y = height - margin

        c.setFont("Helvetica-Bold", 14)
        c.drawString(x, y, "Informe de inspección vehicular")
        y -= 24

        c.setFont("Helvetica", 10)
        lines = []
        lines.append(f"ID: {inspection.id}")
        lines.append(f"Vehículo: {inspection.vehicle_id}")
        lines.append(f"Usuario: {inspection.username or inspection.user_id}")
        lines.append(f"Fecha: {inspection.created_at}")
        lines.append("")
        lines.append("Resumen:")
        for line in (inspection.report or "").splitlines():
            lines.append(line)

        for line in lines:
            if y < margin + 80:
                c.showPage()
                y = height - margin
                c.setFont("Helvetica", 10)
            c.drawString(x, y, str(line))
            y -= 14

        # Try to include diff image if available
        if inspection.diff_image:
            try:
                img_filename = Path(inspection.diff_image).name
                img_full = STATIC_INSPECTIONS_DIR / img_filename
                if img_full.exists():
                    c.showPage()
                    img = Image.open(str(img_full))
                    iw, ih = img.size
                    max_w = width - margin * 2
                    max_h = height - margin * 2
                    scale = min(max_w / iw, max_h / ih, 1.0)
                    draw_w = iw * scale
                    draw_h = ih * scale
                    img_reader = ImageReader(str(img_full))
                    c.drawImage(img_reader, margin + (max_w - draw_w) / 2, margin + (max_h - draw_h) / 2, width=draw_w, height=draw_h)
            except Exception:
                pass

        c.save()

    try:
        await asyncio.to_thread(build_pdf)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error generando PDF: {e}")

    inspection.pdf_file = f"/static/inspections/{pdf_filename}"
    session.add(inspection)
    await session.commit()
    await session.refresh(inspection)

    inspection_response = inspection.model_dump()
    return inspection_response


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
    "/inspection/compare",
    response_model=VehicleInspectionRead,
    status_code=status.HTTP_201_CREATED,
)
async def compare_vehicle_inspection(
    vehicle_id: uuid.UUID = Form(...),
    before_images: List[UploadFile] = File(...),
    after_images: List[UploadFile] = File(...),
    notes: Optional[str] = Form(None),
    fuel_level: Optional[str] = Form(None),
    tire_condition: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        vehicle = await session.get(Vehicle, vehicle_id)
        if not vehicle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vehiculo no encontrado.",
            )

        if vehicle.user_id != user.id and not (user.is_superuser or user.level in (1, 2)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acceso denegado. Solo el usuario asignado o un supervisor/admin puede inspeccionar.",
            )

        if not before_images or not after_images:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Debes enviar al menos una imagen de salida y una de regreso.",
            )

        before_image_paths = []
        after_image_paths = []

        before_bytes_list = []
        after_bytes_list = []
        first_before_filename = None
        first_after_filename = None

        for idx, file in enumerate(before_images, start=1):
            suffix = Path(file.filename or f"before_{idx}").suffix or ".jpg"
            filename = f"{uuid.uuid4().hex}_before_{idx}{suffix}"
            path = STATIC_INSPECTIONS_DIR / filename
            content = await file.read()
            path.write_bytes(content)
            before_image_paths.append(f"/static/inspections/{filename}")
            before_bytes_list.append(content)
            if idx == 1:
                first_before_filename = filename

        for idx, file in enumerate(after_images, start=1):
            suffix = Path(file.filename or f"after_{idx}").suffix or ".jpg"
            filename = f"{uuid.uuid4().hex}_after_{idx}{suffix}"
            path = STATIC_INSPECTIONS_DIR / filename
            content = await file.read()
            path.write_bytes(content)
            after_image_paths.append(f"/static/inspections/{filename}")
            after_bytes_list.append(content)
            if idx == 1:
                first_after_filename = filename

        if not before_bytes_list or not after_bytes_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se pudieron procesar las imágenes de salida o regreso.",
            )

        before_suffix = Path(first_before_filename or "before").suffix or ".jpg"
        after_suffix = Path(first_after_filename or "after").suffix or ".jpg"
        diff_filename = f"{uuid.uuid4().hex}_diff{after_suffix}"
        diff_path = STATIC_INSPECTIONS_DIR / diff_filename

        try:
            before_imgs = [Image.open(BytesIO(b)).convert("RGB") for b in before_bytes_list]
            after_imgs = [Image.open(BytesIO(b)).convert("RGB") for b in after_bytes_list]
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se pudo procesar las imágenes. Envía archivos de imagen válidos.",
            ) from exc

        def normalize_pair(b_img: Image.Image, a_img: Image.Image):
            if b_img.size != a_img.size:
                min_width = min(b_img.width, a_img.width)
                min_height = min(b_img.height, a_img.height)
                b_img = b_img.crop((0, 0, min_width, min_height))
                a_img = a_img.crop((0, 0, min_width, min_height))
            return b_img, a_img

        def estimate_translation(b_img: Image.Image, a_img: Image.Image):
            small_width = 256
            aspect = b_img.height / max(1, b_img.width)
            small_height = max(64, int(round(small_width * aspect)))
            b_small = np.array(b_img.convert("L").resize((small_width, small_height), resample=Image.BILINEAR)).astype(np.float32)
            a_small = np.array(a_img.convert("L").resize((small_width, small_height), resample=Image.BILINEAR)).astype(np.float32)

            b_small -= b_small.mean()
            a_small -= a_small.mean()
            f1 = np.fft.fft2(b_small)
            f2 = np.fft.fft2(a_small)
            R = f1 * np.conj(f2)
            denom = np.abs(R)
            denom[denom == 0] = 1.0
            cross = R / denom
            corr = np.fft.ifft2(cross)
            corr = np.abs(corr)
            max_idx = np.unravel_index(np.argmax(corr), corr.shape)
            shift_y = int(max_idx[0] if max_idx[0] < small_height / 2 else max_idx[0] - small_height)
            shift_x = int(max_idx[1] if max_idx[1] < small_width / 2 else max_idx[1] - small_width)
            scale_x = b_img.width / float(small_width)
            scale_y = b_img.height / float(small_height)
            return int(round(shift_x * scale_x)), int(round(shift_y * scale_y))

        def align_pair(b_img: Image.Image, a_img: Image.Image):
            b_img, a_img = normalize_pair(b_img, a_img)
            shift_x, shift_y = estimate_translation(b_img, a_img)
            if abs(shift_x) > b_img.width * 0.25 or abs(shift_y) > b_img.height * 0.25:
                return b_img, a_img

            w, h = b_img.size
            x0 = max(0, shift_x)
            y0 = max(0, shift_y)
            x1 = min(w, w + shift_x) if shift_x < 0 else min(w, w - shift_x)
            y1 = min(h, h + shift_y) if shift_y < 0 else min(h, h - shift_y)

            if x1 <= x0 or y1 <= y0:
                return b_img, a_img

            if shift_x >= 0:
                b_crop = b_img.crop((x0, 0, w, h))
                a_crop = a_img.crop((0, 0, w - shift_x, h))
            else:
                b_crop = b_img.crop((0, 0, w + shift_x, h))
                a_crop = a_img.crop((-shift_x, 0, w, h))

            if shift_y >= 0:
                b_crop = b_crop.crop((0, y0, b_crop.width, h))
                a_crop = a_crop.crop((0, 0, a_crop.height - shift_y, a_crop.height))
            else:
                b_crop = b_crop.crop((0, 0, b_crop.width, h + shift_y))
                a_crop = a_crop.crop((0, -shift_y, a_crop.width, h))

            return b_crop, a_crop

        def build_diff_mask(b_img: Image.Image, a_img: Image.Image):
            b_img, a_img = normalize_pair(b_img, a_img)
            b_img, a_img = align_pair(b_img, a_img)
            diff = ImageChops.difference(b_img, a_img)
            diff_gray = diff.convert("L")
            diff_thresh = diff_gray.point(lambda p: 255 if p > 12 else 0)
            edges_before = b_img.convert("L").filter(ImageFilter.FIND_EDGES)
            edges_after = a_img.convert("L").filter(ImageFilter.FIND_EDGES)
            edge_diff = ImageChops.difference(edges_before, edges_after).convert("L")
            edge_thresh = edge_diff.point(lambda p: 255 if p > 18 else 0)
            diff_arr = np.array(diff_thresh)
            edge_arr = np.array(edge_thresh)
            combined = ((diff_arr > 0) | (edge_arr > 0)).astype(np.uint8) * 255
            mask = make_mask_from_array(combined, blur_radius=0.8, thresh=20)
            mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
            return mask

        def build_vehicle_mask_from_images(images, target_size):
            masks = []
            for img in images:
                mask = estimate_vehicle_mask(img)
                if mask is None:
                    continue
                if mask.size != target_size:
                    mask = mask.resize(target_size, resample=Image.NEAREST)
                masks.append(np.array(mask))
            if not masks:
                return None
            combined = np.maximum.reduce(masks).astype(np.uint8)
            combined_img = Image.fromarray(combined, mode="L")
            combined_img = combined_img.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(2)).point(lambda p: 255 if p > 30 else 0)
            return combined_img

        vehicle_mask = build_vehicle_mask_from_images(before_imgs + after_imgs, normalize_pair(before_imgs[0], after_imgs[0])[0].size)
        pair_images = list(product(before_imgs, after_imgs))

        masks = []
        if vehicle_mask is not None:
            vehicle_mask = vehicle_mask.filter(ImageFilter.MaxFilter(11)).filter(ImageFilter.GaussianBlur(3)).point(lambda p: 255 if p > 30 else 0)
            vehicle_arr = np.array(vehicle_mask)
            for b_img, a_img in pair_images:
                diff_arr = np.array(build_diff_mask(b_img, a_img))
                filtered_arr = np.where(vehicle_arr > 0, diff_arr, 0).astype(np.uint8)
                masks.append(filtered_arr)

            if not any(mask.sum() > 0 for mask in masks):
                masks = [np.array(build_diff_mask(b_img, a_img)) for b_img, a_img in pair_images]
        else:
            masks = [np.array(build_diff_mask(b_img, a_img)) for b_img, a_img in pair_images]

        if not masks:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo generar la máscara de diferencias.",
            )

        combined_mask_arr = np.maximum.reduce(masks)
        diff_mask = Image.fromarray(combined_mask_arr, mode="L")
        diff_gray = diff_mask.copy()
        after_img = after_imgs[0]

        total_pixels_est = diff_mask.width * diff_mask.height
        changed_pixels_est = sum(1 for px in diff_mask.getdata() if px)
        changed_frac = (changed_pixels_est / total_pixels_est) if total_pixels_est else 0.0
        if changed_frac > 0.20:
            combined_mask_img = Image.fromarray(combined_mask_arr, mode="L")
            diff_mask = make_mask_from_array(np.array(combined_mask_img), blur_radius=0.6, thresh=28)
            if vehicle_mask is not None:
                vehicle_arr = np.array(vehicle_mask.resize(diff_mask.size, resample=Image.NEAREST))
                diff_arr = np.array(diff_mask)
                filtered_arr = np.where(vehicle_arr > 0, diff_arr, 0).astype(np.uint8)
                if filtered_arr.sum() > 0:
                    diff_mask = Image.fromarray(filtered_arr, mode="L")

        width, height = diff_mask.size
        regions = extract_connected_regions(diff_mask, min_pixels=18)

        if not regions and diff_mask.getbbox():
            bx = diff_mask.getbbox()
            pixel_count = sum(1 for pixel in diff_mask.getdata() if pixel == 255)
            area_frac = (pixel_count / (width * height)) if (width * height) else 0.0
            if area_frac < 0.60:
                regions.append({"bbox": bx, "pixel_count": pixel_count})

        split_regions = []
        for region in regions:
            bbox = region["bbox"]
            region_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if region_area > 0 and region_area / (width * height) > 0.14:
                split_regions.extend(split_large_region(bbox, diff_gray))
            else:
                split_regions.append(region)
        regions = split_regions

        total_pixels = width * height
        min_region_area = max(120, int(total_pixels * 0.00012))
        filtered_regions = []
        for region in regions:
            bbox = region["bbox"]
            region_width = bbox[2] - bbox[0]
            region_height = bbox[3] - bbox[1]
            if region["pixel_count"] < min_region_area or region_width < 10 or region_height < 10:
                continue

            crop = after_img.crop(bbox).convert("RGB")
            if not is_vehicle_like_region(crop, min_confidence=0.16):
                continue

            filtered_regions.append(region)

        if filtered_regions:
            regions = filtered_regions

        changed_pixels = sum(1 for pixel in diff_mask.getdata() if pixel)
        total_pixels = width * height
        change_percent = round((changed_pixels / total_pixels) * 100, 2) if total_pixels else 0.0
        score = round(max(0.0, min(100.0, 100.0 - change_percent)), 1)

        output_image = after_img.convert("RGBA")
        draw = PILImageDraw.Draw(output_image)

        issue_summaries = []
        recommendations = []
        summary_tags = []

        def determine_vehicle_section(label: Optional[str], bbox, width: int, height: int):
            if label:
                normalized = label.lower()
                if any(keyword in normalized for keyword in ("neumático", "rueda", "tire", "wheel", "llanta", "llanta")):
                    return "Neumáticos / ruedas"
                if any(keyword in normalized for keyword in ("capó", "hood", "parachoques", "frontal", "front")):
                    return "Frontal"
                if any(keyword in normalized for keyword in ("trasero", "trasera", "cola", "caja", "rear", "back")):
                    return "Trasera"
                if any(keyword in normalized for keyword in ("techo", "cabina", "parabrisas", "windshield", "roof")):
                    return "Techo / cabina"
                if any(keyword in normalized for keyword in ("foco", "faros", "luz", "headlight", "light")):
                    return "Focos / iluminación"
                if any(keyword in normalized for keyword in ("vidrio", "cristal", "parabrisas", "glass", "windshield")):
                    return "Vidrio / cristal"
                if any(keyword in normalized for keyword in ("puerta", "door", "lado", "lateral")):
                    return "Lateral"

            x0, y0, x1, y1 = bbox
            x_center = (x0 + x1) / 2.0
            y_center = (y0 + y1) / 2.0
            norm_x = x_center / max(1.0, width)
            norm_y = y_center / max(1.0, height)

            if norm_y < 0.25:
                return "Techo / cabina"
            if norm_y > 0.75:
                return "Neumáticos / ruedas"
            if norm_x < 0.33:
                return "Lado izquierdo"
            if norm_x > 0.67:
                return "Lado derecho"
            return "Centro / carrocería"

        for idx, region in enumerate(regions, start=1):
            x0, y0, x1, y1 = region["bbox"]
            pad = 8
            x0 = max(0, x0 - pad)
            y0 = max(0, y0 - pad)
            x1 = min(width, x1 + pad)
            y1 = min(height, y1 + pad)
            region_width = x1 - x0
            region_height = y1 - y0
            region_area = region["pixel_count"]
            area_pct = round((region_area / total_pixels) * 100, 2) if total_pixels else 0.0
            region_crop = diff_gray.crop((x0, y0, x1, y1))
            crop_pixels = list(region_crop.getdata())
            avg_diff = sum(crop_pixels) / len(crop_pixels) if crop_pixels else 0

            color_crop = after_img.crop((x0, y0, x1, y1)).convert("RGB")
            bio = BytesIO()
            color_crop.save(bio, format="PNG")
            bio_bytes = bio.getvalue()

            vehicle_check_labels = [
                "vehicle",
                "car",
                "truck",
                "background",
                "road",
                "ground",
                "dirt",
                "rock",
                "grass",
                "gravel",
                "pavement",
            ]
            vehicle_check = classify_image_bytes(bio_bytes, labels=vehicle_check_labels)
            vehicle_check_label = vehicle_check.get("label")
            vehicle_check_conf = float(vehicle_check.get("confidence") or 0.0)
            vehicle_check_engine = vehicle_check.get("engine")
            if vehicle_check_engine == "clip":
                if vehicle_check_label not in {"vehicle", "car", "truck"}:
                    if vehicle_check_label in {"background", "road", "ground", "dirt", "rock", "grass", "gravel", "pavement"} and vehicle_check_conf > 0.45:
                        continue

            damage_label_set = [
                "Daño en pintura / carrocería",
                "Rasguño / fisura",
                "Daño en neumático / rueda",
                "Foco / luz",
                "Vidrio / cristal",
                "Suciedad / manchas",
                "Suciedad / iluminación",
                "Cambio visual",
            ]
            cls = classify_image_bytes(bio_bytes, labels=damage_label_set)
            cls_label = cls.get("label")
            cls_conf = float(cls.get("confidence") or 0.0)
            cls_engine = cls.get("engine")

            section = determine_vehicle_section(cls_label, (x0, y0, x1, y1), width, height)

            aspect_ratio = region_width / max(1, region_height)
            color_crop_gray = color_crop.convert("L")
            crop_arr = np.array(color_crop_gray)
            mean = float(crop_arr.mean())
            edges = color_crop_gray.filter(ImageFilter.FIND_EDGES)
            edge_arr = np.array(edges)
            edge_density = float((edge_arr > 20).sum()) / max(1, region_width * region_height)
            contrast = float(crop_arr.std())

            recommendation_map = {
                "Daño en pintura / carrocería": "Revisa la pintura y considera reparación local o repintado según la profundidad del daño.",
                "Rasguño / fisura": "Inspecciona la superficie, evalúa pulido o reparación si el rayón es profundo.",
                "Rasguño / abolladura": "Inspecciona la superficie, limpia el área y evalúa pulido o reparación si el rayón es profundo.",
                "Daño en neumático / rueda": "Revisa la presión y la integridad del neumático; lleva a taller si es necesario.",
                "Foco / luz": "Revisa el foco y el vidrio del faro; reemplaza o ajusta si hay fisuras o pérdida de hermeticidad.",
                "Vidrio / cristal": "Inspecciona el vidrio y parabrisas por fisuras o raspaduras; considera reemplazo si afecta la visibilidad.",
                "Suciedad / manchas": "Limpia el área y vuelve a inspeccionar con buena iluminación para confirmar.",
                "Suciedad / iluminación": "Limpia el área y vuelve a inspeccionar con buena iluminación para confirmar.",
                "Cambio visual": "Revisa la zona y vuelve a inspeccionar con buena iluminación para confirmar.",
            }

            if cls_label:
                label = cls_label
                confidence = cls_conf
                engine = cls_engine
                recommendation = recommendation_map.get(label, "Revisa la zona y vuelve a inspeccionar con buena iluminación.")
            else:
                confidence = 0.0
                engine = "heuristic"
                if avg_diff > 100 or area_pct > 2.0 or contrast > 45:
                    label = "Daño en pintura / carrocería"
                    recommendation = "Revisa la pintura y considera reparación local o repintado según la profundidad del daño."
                elif aspect_ratio > 3.0 or 1 / aspect_ratio > 3.0:
                    label = "Rasguño / fisura"
                    recommendation = "Inspecciona la superficie, evalúa pulido o reparación si el rayón es profundo."
                elif 0.65 <= aspect_ratio <= 1.45 and region_area > total_pixels * 0.0012 and edge_density > 0.035:
                    label = "Daño en neumático / rueda"
                    recommendation = "Revisa la presión y la integridad del neumático; lleva a taller si es necesario."
                elif mean > 200 and edge_density > 0.025:
                    label = "Foco / luz"
                    recommendation = "Revisa el foco y el vidrio del faro; reemplaza o ajusta si hay fisuras o pérdida de hermeticidad."
                elif 0.8 <= aspect_ratio <= 1.6 and edge_density > 0.04:
                    label = "Vidrio / cristal"
                    recommendation = "Inspecciona el vidrio y parabrisas por fisuras o raspaduras; considera reemplazo si afecta la visibilidad."
                elif avg_diff > 40 or region_area > total_pixels * 0.0015:
                    label = "Rasguño / abolladura"
                    recommendation = "Inspecciona la superficie, limpia el área y evalúa pulido o reparación si el rayón es profundo."
                else:
                    label = "Suciedad / iluminación"
                    recommendation = "Limpia el área y vuelve a inspeccionar con buena iluminación para confirmar."

            issue_summaries.append(
                {
                    "idx": idx,
                    "label": label,
                    "confidence": float(confidence) if 'confidence' in locals() else 0.0,
                    "engine": engine if 'engine' in locals() else 'heuristic',
                    "bbox": (x0, y0, x1, y1),
                    "width": region_width,
                    "height": region_height,
                    "area_pct": area_pct,
                    "recommendation": recommendation,
                    "section": section,
                }
            )
            if recommendation not in recommendations:
                recommendations.append(recommendation)
            if section and f"Sección: {section}" not in summary_tags:
                summary_tags.append(f"Sección: {section}")

            draw.rectangle([x0, y0, x1, y1], outline=(255, 0, 0, 255), width=8)
            draw.rectangle([x0 - 5, y0 - 5, x1 + 5, y1 + 5], outline=(200, 0, 0, 180), width=3)

        output_image.save(diff_path)

        used_clip = any(issue["engine"] == "clip" for issue in issue_summaries)
        analysis_engine_label = "Soland Vision AI (CLIP)" if used_clip else "Soland Vision AI (heuristic)"
        analysis_mode_label = "CLIP zero-shot" if used_clip else "Heurística de respaldo"

        report_lines = [
            f"Informe de inspección visual automática ({change_percent:.1f}% de cambio detectado).",
            f"Puntaje de condición estimado: {score:.1f}%.",
            f"Motor de análisis: {analysis_engine_label}.",
            f"Modo de análisis: {analysis_mode_label}.",
        ]

        if issue_summaries:
            report_lines.append(f"Se detectaron {len(issue_summaries)} fallas visuales relevantes.")
            for issue in issue_summaries:
                report_lines.append(
                    f"{issue['idx']}) {issue['label']} en {issue.get('section', 'zona indefinida')} (x={issue['bbox'][0]},y={issue['bbox'][1]},w={issue['width']},h={issue['height']}) (~{issue['area_pct']}% área). Recomendación: {issue['recommendation']}"
                )
            report_lines.append("Mejoras recomendadas:")
            for rec in recommendations:
                report_lines.append(f"- {rec}")
        else:
            report_lines.append(
                "No se detectaron diferencias visuales relevantes. Mantén el vehículo limpio y vuelve a inspeccionar con buena iluminación."
            )

        if tire_condition:
            report_lines.append(f"Condición de cauchos: {tire_condition}.")
            summary_tags.append(f"Cauchos: {tire_condition}")

        if fuel_level:
            report_lines.append(f"Estado de gasolina: {fuel_level}.")
            summary_tags.append(f"Gasolina: {fuel_level}")

        if any(issue['label'] in {'Suciedad / iluminación', 'Suciedad / manchas'} for issue in issue_summaries):
            report_lines.append(
                "Algunas diferencias parecen corresponder a suciedad o variaciones de iluminación; limpia y vuelve a inspeccionar."
            )

        report_lines.append(f"Imagen de diferencias: /static/inspections/{diff_filename}")
        report_lines.append(f"Notas: {notes or 'Sin notas adicionales.'}")
        report = "\n".join(report_lines)

        inspection = VehicleInspection(
            vehicle_id=vehicle_id,
            user_id=user.id,
            username=getattr(user, "username", None),
            before_image=f"/static/inspections/{first_before_filename}",
            after_image=f"/static/inspections/{first_after_filename}",
            diff_image=f"/static/inspections/{diff_filename}",
            before_images=before_image_paths,
            after_images=after_image_paths,
            fuel_level=fuel_level,
            tire_condition=tire_condition,
            summary_tags=summary_tags,
            report=report,
            score=score,
            change_percent=change_percent,
            notes=notes,
        )
        session.add(inspection)
        await session.commit()
        await session.refresh(inspection)

        inspection_response = inspection.model_dump()
        inspection_response["issues"] = issue_summaries
        inspection_response["recommendations"] = recommendations
        inspection_response["analysis_engine"] = analysis_engine_label
        inspection_response["analysis_mode"] = analysis_mode_label
        inspection_response["before_images"] = before_image_paths
        inspection_response["after_images"] = after_image_paths
        inspection_response["fuel_level"] = fuel_level
        inspection_response["tire_condition"] = tire_condition
        inspection_response["summary_tags"] = summary_tags
        return inspection_response
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"No se pudo completar la inspección: {exc}",
        ) from exc


@router.get(
    "/inspection/history",
    response_model=List[VehicleInspectionRead],
)
async def get_vehicle_inspection_history(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    query = select(VehicleInspection)
    if not (user.is_superuser or user.level in (1, 2)):
        query = query.where(VehicleInspection.user_id == user.id)
    query = query.order_by(VehicleInspection.created_at.desc())

    result = await session.execute(query)
    return result.scalars().all()


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
