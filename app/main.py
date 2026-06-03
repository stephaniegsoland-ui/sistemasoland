from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.db import create_db_and_tables, engine
from app.core.auth import auth_backend, fastapi_users
from app.schemas.user import UserRead, UserCreate, UserUpdate
from app.api.endpoints import categories, inventary, type_record, vehicle


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    yield
    await engine.dispose()


app = FastAPI(
    title="SOLAND API",
    description="Backend para gestion de EPP e IA",
    lifespan=lifespan,
)

# Middleware Settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth Settings
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
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/api/users",
    tags=["Gestion de Usuarios"],
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
