import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, status

from app.core.auth import current_active_user
from app.models.user import User

STATIC_PEAJES_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "peajes"
STATIC_PEAJES_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()


@router.get("/", status_code=status.HTTP_200_OK)
async def list_peajes(user: User = Depends(current_active_user)):
    # For now return an empty list; UI may merge with local submissions
    return []


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_peaje(
    driver: str = Form(...),
    vehicle: str = Form(...),
    plate: str = Form(...),
    route: str = Form(...),
    amount: str = Form(...),
    date: str = Form(...),
    type: str = Form(...),
    notes: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: User = Depends(current_active_user),
):
    # basic validation
    try:
        _ = float(amount)
    except Exception:
        raise HTTPException(status_code=400, detail="Monto inválido")

    # save file
    suffix = Path(file.filename or "file").suffix or ".jpg"
    filename = f"{uuid.uuid4().hex}{suffix}"
    dest = STATIC_PEAJES_DIR / filename
    contents = await file.read()
    dest.write_bytes(contents)

    # build response
    created = {
        "id": uuid.uuid4().hex,
        "driver": driver,
        "vehicle": vehicle,
        "plate": plate,
        "route": route,
        "amount": float(amount),
        "date": date,
        "type": type,
        "notes": notes,
        "file": f"/static/peajes/{filename}",
        "created_at": datetime.utcnow().isoformat(),
        "created_by": getattr(user, "id", None),
    }

    # not persisting to DB for now — simply return created object
    return created
