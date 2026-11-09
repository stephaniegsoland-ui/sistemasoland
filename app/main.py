import os
import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.db import create_db_and_tables, engine, repair_legacy_user_photos
from app.core.auth import auth_backend, ensure_default_admin_user, fastapi_users
from app.core.storage import MEDIA_ROOT
from app.core.document_reminders import document_expiry_reminder_loop
from app.schemas.user import UserRead, UserCreate, UserUpdate
from app.api.endpoints import (
    categories, inventary, notifications, type_record, vehicle, procura, timesheet,
    chat, users, security, peaje, safety_permits, companies, environment, documents,
    admin_overview, assistant, debug_photos, settings, login,
)


def sync_inspection_static_dirs() -> None:
    app_dir = Path(__file__).resolve().parent
    target_dir = MEDIA_ROOT / "inspections"
    old_dir = app_dir / "api" / "static" / "inspections"
    if not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)

    if old_dir.exists() and old_dir.is_dir():
        for item in old_dir.iterdir():
            if item.is_file():
                destination = target_dir / item.name
                if not destination.exists():
                    item.rename(destination)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sync_inspection_static_dirs()
    await create_db_and_tables()
    await ensure_default_admin_user()
    await repair_legacy_user_photos()
    reminder_task = asyncio.create_task(document_expiry_reminder_loop())
    try:
        yield
    finally:
        reminder_task.cancel()
        with suppress(asyncio.CancelledError):
            await reminder_task
        await engine.dispose()


def get_allowed_origins() -> list[str]:
    default_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://frontsoland.vercel.app",
        "https://www.soland.com",
        "https://soland.com",
        "https://api.soland.com",
        "https://sistemasoland.onrender.com",
    ]
    env_origins = os.getenv("CORS_ALLOWED_ORIGINS")
    if not env_origins:
        if os.getenv("APP_ENV", "development").lower() in {"production", "prod"}:
            raise RuntimeError("CORS_ALLOWED_ORIGINS must be configured in production")
        return list(dict.fromkeys(default_origins))

    parsed_origins = [
        origin.strip()
        for origin in env_origins.split(",")
        if origin.strip()
    ]
    return list(dict.fromkeys(parsed_origins))


app = FastAPI(
    title="SOLAND API",
    description="Backend para gestion de EPP e IA",
    lifespan=lifespan,
)

@app.get("/health", tags=["Sistema"])
async def health_check():
    return {"status": "ok"}

# Middleware Settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth Settings
app.include_router(login.router, prefix="/api/auth", tags=["Autenticación"])

app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/api/auth",
    tags=["Autenticacion"],
)

app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/api/auth",
    tags=["Autenticacion"],
)

app.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/api/auth",
    tags=["Autenticacion"],
)

app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/api/users",
    tags=["Gestion de Usuarios"],
)

app.include_router(
    users.router, prefix="/api/users", tags=["Gestion de Usuarios"]
)

app.mount(
    "/static",
    StaticFiles(directory=MEDIA_ROOT),
    name="static",
)

app.include_router(
    categories.router, prefix="/api/categories", tags=["Gestión de Categorías"]
)

# 6. Módulo de Inventario
app.include_router(
    inventary.router, prefix="/api/inventary", tags=["Gestión de Inventario"]
)

app.include_router(
    type_record.router, prefix="/api/type_record", tags=["Gestion de  Flota - Tipos"]
)

app.include_router(
    vehicle.router, prefix="/api/vehicle", tags=["Gestion de Flota - Vehículos"]
)

app.include_router(
    security.router, prefix="/api/security", tags=["Seguridad EPP"]
)

app.include_router(
    safety_permits.router, prefix="/api/security", tags=["Permisos y Riesgos"]
)

if os.getenv("APP_ENV", "development").lower() not in {"production", "prod"}:
    app.include_router(debug_photos.router, prefix="/api/debug", tags=["Debug"])

app.include_router(
    procura.router, prefix="/api/procura", tags=["Gestión de Procura"]
)

app.include_router(
    timesheet.router, prefix="/api/timesheet", tags=["Gestión de Hoja de Tiempo"]
)

app.include_router(chat.router, prefix="/api/chat", tags=["Chat Interno"])

app.include_router(
    notifications.router, prefix="/api/notifications", tags=["Notificaciones"]
)

app.include_router(
    assistant.router, prefix="/api/assistant", tags=["Asistente IA"]
)

app.include_router(
    companies.router, prefix="/api/admin/companies", tags=["Empresas asociadas"]
)

app.include_router(
    admin_overview.router, prefix="/api/admin", tags=["Administración"]
)

app.include_router(
    environment.router, prefix="/api/environment", tags=["Departamento Ambiental"]
)

app.include_router(
    documents.router, prefix="/api/documents", tags=["Gestión Documental"]
)

app.include_router(
    settings.router, prefix="/api/settings", tags=["Configuración"]
)

# Peaje endpoint for vehicle toll submissions (used by frontend /dashboard/vehiculos/peaje)
app.include_router(peaje.router, prefix="/api/admin/peaje", tags=["Peajes"])
