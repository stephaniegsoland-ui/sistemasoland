from typing import List, Dict
from pathlib import Path
import base64

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.db import get_async_session
from app.models.user import User
from app.core.auth import get_supervisor_or_admin

router = APIRouter()


def decode_photo_data(photo_data: str) -> bytes:
    if photo_data.startswith("data:"):
        try:
            _, encoded = photo_data.split(",", 1)
        except ValueError:
            encoded = photo_data
    else:
        encoded = photo_data
    return base64.b64decode(encoded)


@router.get("/user-photos")
async def list_user_photos(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_supervisor_or_admin),
):
    """Dev helper: lista usuarios y si su foto está en `app/static/users` o en DB blob.
    Requiere usuario administrador/supervisor.
    """
    result = await session.execute(select(User))
    users = result.scalars().all()
    base_dir = Path(__file__).resolve().parent.parent.parent / "static" / "users"
    out: List[Dict] = []
    for u in users:
        entry = {
            "id": str(u.id),
            "username": getattr(u, "username", None),
            "photo_path": getattr(u, "photo_path", None),
            "has_blob": bool(getattr(u, "photo_data", None)),
            "file_exists": False,
            "file_size": None,
        }
        if u.photo_path:
            try:
                # normalize and resolve
                p = Path(str(u.photo_path).lstrip("/"))
                candidate = Path(__file__).resolve().parent.parent.parent / p
                if candidate.exists() and candidate.is_file():
                    entry["file_exists"] = True
                    entry["file_size"] = candidate.stat().st_size
            except Exception:
                pass
        out.append(entry)

    return {"count": len(out), "users": out, "static_users_dir": str(base_dir)}
