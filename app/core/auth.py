import asyncio
import os
import smtplib
import uuid
import logging
import jwt
from email.message import EmailMessage
from typing import Optional
from fastapi import Depends, Request, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlmodel import select
from urllib.parse import urlencode, urlsplit
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, exceptions
from fastapi_users.password import PasswordHelper
from fastapi_users.jwt import decode_jwt, generate_jwt
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)

from app.models.user import User
from app.core.db import engine, get_user_db
from app.core.storage import media_dir
import base64
from pathlib import Path

STATIC_USERS_DIR = media_dir("users")

logger = logging.getLogger("soland.auth")
if not logger.handlers:
    # basic configuration for simple console logging during development
    logging.basicConfig(level=logging.INFO)

SECRET = os.getenv("SECRET")
DEFAULT_ADMIN_USERNAME = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_EMAIL = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@soland.com")
APP_ENV = os.getenv("APP_ENV", "development").lower()
DEFAULT_ADMIN_PASSWORD = os.getenv(
    "DEFAULT_ADMIN_PASSWORD",
    None if APP_ENV in {"production", "prod"} else "admin123",
)
BOOTSTRAP_ADMIN = os.getenv(
    "BOOTSTRAP_ADMIN",
    "false" if APP_ENV in {"production", "prod"} else "true",
).lower() in {"1", "true", "yes", "on"}

if not SECRET:
    raise RuntimeError("SECRET must be configured before starting the API")

if APP_ENV in {"production", "prod"} and not DEFAULT_ADMIN_PASSWORD:
    raise RuntimeError("DEFAULT_ADMIN_PASSWORD must be configured in production")


async def ensure_default_admin_user() -> None:
    if not BOOTSTRAP_ADMIN or not DEFAULT_ADMIN_PASSWORD:
        logger.info("Default admin bootstrap skipped")
        return

    async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    password_helper = PasswordHelper()

    async with async_session_maker() as session:
        existing_user = (await session.execute(select(User).where(User.username == DEFAULT_ADMIN_USERNAME))).scalar_one_or_none()
        if existing_user is None:
            user = User(
                username=DEFAULT_ADMIN_USERNAME,
                email=DEFAULT_ADMIN_EMAIL,
                level=1,
                is_active=True,
                is_superuser=True,
                is_verified=True,
                hashed_password=password_helper.hash(DEFAULT_ADMIN_PASSWORD),
            )
            session.add(user)
            await session.commit()
            logger.info("Created default admin user '%s' with email '%s'", DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_EMAIL)
            return

        try:
            password_matches = password_helper.verify(DEFAULT_ADMIN_PASSWORD, existing_user.hashed_password)
        except Exception:
            password_matches = False

        if password_matches:
            return

        existing_user.email = existing_user.email or DEFAULT_ADMIN_EMAIL
        existing_user.level = 1
        existing_user.is_active = True
        existing_user.is_superuser = True
        existing_user.is_verified = True
        existing_user.hashed_password = password_helper.hash(DEFAULT_ADMIN_PASSWORD)

        session.add(existing_user)
        await session.commit()
        logger.info("Reset default admin user '%s' to the known local password", DEFAULT_ADMIN_USERNAME)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    reset_password_token_lifetime_seconds = 1800
    verification_token_secret = SECRET

    async def validate_password(self, password: str, user: User) -> None:
        if not 8 <= len(password) <= 128:
            raise exceptions.InvalidPasswordException(
                "La contraseña debe tener entre 8 y 128 caracteres."
            )
        await super().validate_password(password, user)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ) -> None:
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
        parsed_frontend_url = urlsplit(frontend_url)
        if not parsed_frontend_url.scheme or not parsed_frontend_url.netloc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No fue posible enviar el correo de recuperación.",
            )
        if APP_ENV in {"production", "prod"} and parsed_frontend_url.scheme != "https":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No fue posible enviar el correo de recuperación.",
            )

        smtp_host = os.getenv("SMTP_HOST")
        smtp_port_value = os.getenv("SMTP_PORT", "587")
        smtp_username = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv("SMTP_PASSWORD")
        sender = os.getenv("SMTP_FROM_EMAIL")
        if not all((smtp_host, smtp_username, smtp_password, sender)):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No fue posible enviar el correo de recuperación.",
            )

        try:
            smtp_port = int(smtp_port_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No fue posible enviar el correo de recuperación.",
            ) from exc
        if not 1 <= smtp_port <= 65535:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No fue posible enviar el correo de recuperación.",
            )

        reset_url = (
            f"{frontend_url}/reset-password?"
            f"{urlencode({'token': token})}"
        )
        message = EmailMessage()
        message["Subject"] = "Restablece tu contraseña de Soland"
        message["From"] = sender
        message["To"] = user.email
        message.set_content(
            "Recibimos una solicitud para cambiar la contraseña de tu cuenta de Soland.\n\n"
            f"Abre este enlace para elegir una contraseña nueva (válido por 30 minutos):\n{reset_url}\n\n"
            "Si no solicitaste este cambio, puedes ignorar este mensaje."
        )

        use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes", "on"}
        use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"}

        def send_email() -> None:
            smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
            with smtp_class(smtp_host, smtp_port, timeout=15) as server:
                if use_tls and not use_ssl:
                    server.starttls()
                server.login(smtp_username, smtp_password)
                server.send_message(message)

        try:
            await asyncio.to_thread(send_email)
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            logger.exception("Password reset email delivery failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No fue posible enviar el correo de recuperación.",
            ) from exc

    async def on_after_reset_password(
        self, user: User, request: Optional[Request] = None
    ) -> None:
        user.session_version += 1
        await self.user_db.update(user, {"session_version": user.session_version})

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        # Save photo_data to disk (if provided) and update user's photo_path
        try:
            pd = getattr(user, "photo_data", None)
            if pd:
                logger.info("on_after_register: received photo_data for user %s", user.id)
                photo_data = pd
                if photo_data.startswith("data:"):
                    try:
                        _, encoded = photo_data.split(",", 1)
                    except ValueError:
                        logger.warning("photo_data for user %s has invalid data: URL prefix but no comma", user.id)
                        return
                else:
                    encoded = photo_data

                try:
                    data = base64.b64decode(encoded)
                except Exception as e:
                    logger.exception("Failed to base64-decode photo_data for user %s: %s", user.id, e)
                    return

                filename = f"{user.id}.png"
                target = STATIC_USERS_DIR / filename
                try:
                    target.write_bytes(data)
                    logger.info("Wrote user photo to %s", str(target))
                except Exception as e:
                    logger.exception("Failed to write photo file for user %s: %s", user.id, e)

                # update user record with photo_path and clear photo_data to save space
                try:
                    await self.user_db.update(user, {"photo_path": f"/static/users/{filename}", "photo_data": None})
                    logger.info("Updated user %s photo_path to /static/users/%s", user.id, filename)
                except Exception as e:
                    logger.exception("Failed to update user record for %s: %s", user.id, e)
        except Exception:
            logger.exception("Unexpected error in on_after_register for user %s", getattr(user, "id", "<unknown>"))

    async def authenticate(
        self, credentials: OAuth2PasswordRequestForm
    ) -> Optional[User]:
        query = select(User).where(User.username == credentials.username)
        result = await self.user_db.session.execute(query)
        user = result.scalar_one_or_none()

        # If you can't find it, we'll try searching for it by email (as a backup)
        if user is None:
            try:
                user = await self.get_by_email(credentials.username)
            except Exception:
                self.password_helper.hash(credentials.password)
                return None

        veried, updated_password_hash = self.password_helper.verify_and_update(
            credentials.password, user.hashed_password
        )

        if not veried:
            return None

        if updated_password_hash is not None:
            await self.user_db.update(user, {"hashed_password": updated_password_hash})

        return user


async def get_user_manager(user_db=Depends(get_user_db)):
    yield UserManager(user_db)


ACCESS_TOKEN_LIFETIME_SECONDS = 60 * 60 * 24

bearer_transport = BearerTransport(tokenUrl="api/auth/login")
cookie_transport = CookieTransport(
    cookie_name="access_token",
    cookie_max_age=ACCESS_TOKEN_LIFETIME_SECONDS,
    cookie_path="/",
    cookie_secure=APP_ENV in {"production", "prod"},
    cookie_httponly=True,
    cookie_samesite="lax" if APP_ENV not in {"production", "prod"} else "none",
)


class SessionVersionJWTStrategy(JWTStrategy):
    async def write_token(self, user: User) -> str:
        return generate_jwt(
            {"sub": str(user.id), "aud": self.token_audience, "ver": user.session_version},
            self.encode_key,
            self.lifetime_seconds,
            algorithm=self.algorithm,
        )

    async def read_token(self, token, user_manager):
        if token is None:
            return None
        try:
            payload = decode_jwt(token, self.decode_key, self.token_audience, algorithms=[self.algorithm])
            user_id = payload.get("sub")
            if user_id is None:
                return None
            user = await user_manager.get(user_manager.parse_id(user_id))
            if int(payload.get("ver", 0)) != user.session_version:
                return None
            return user
        except (jwt.PyJWTError, exceptions.InvalidID, exceptions.UserNotExists, ValueError, TypeError):
            return None


def get_jwt_strategy() -> JWTStrategy:
    return SessionVersionJWTStrategy(secret=SECRET, lifetime_seconds=ACCESS_TOKEN_LIFETIME_SECONDS)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

cookie_auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend, cookie_auth_backend])

current_active_user = fastapi_users.current_user(active=True)


async def get_supervisor_or_admin(user: User = Depends(current_active_user)):
    if not getattr(user, "is_superuser", False) and getattr(user, "level", 99) > 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado. Se requiere nivel de Administrador o Supervisor.",
        )
    return user


__all__ = ["get_supervisor_or_admin"]



