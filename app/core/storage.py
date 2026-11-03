import os
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile, status


DEFAULT_MEDIA_ROOT = Path(__file__).resolve().parent.parent / "static"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", str(DEFAULT_MEDIA_ROOT))).expanduser().resolve()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "15")) * 1024 * 1024



def media_dir(*parts: str) -> Path:
    directory = MEDIA_ROOT.joinpath(*parts)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


async def read_upload(
    file: UploadFile,
    *,
    allowed_extensions: Iterable[str],
    allowed_content_types: Iterable[str] = (),
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> bytes:
    filename = (file.filename or "").strip()
    extension = Path(filename).suffix.lower()
    extensions = {value.lower() for value in allowed_extensions}
    content_types = {value.lower() for value in allowed_content_types}

    if not filename or extension not in extensions:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Tipo de archivo no permitido.",
        )
    if file.content_type and content_types and file.content_type.lower() not in content_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Tipo de contenido no permitido.",
        )

    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"El archivo no puede superar {max_bytes // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo está vacío.")
    return b"".join(chunks)



def safe_filename_suffix(filename: str | None, default: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix else default
