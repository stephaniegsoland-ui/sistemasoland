import os
import uuid
import logging
from typing import Optional
from fastapi import Depends, Request, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import select
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)

from app.models.user import User
from app.core.db import get_user_db
import base64
from pathlib import Path

STATIC_USERS_DIR = Path(__file__).resolve().parent.parent / "static" / "users"
STATIC_USERS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("soland.auth")
if not logger.handlers:
    # basic configuration for simple console logging during development
    logging.basicConfig(level=logging.INFO)

SECRET = os.getenv("SECRET")


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

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


bearer_transport = BearerTransport(tokenUrl="api/auth/login")
cookie_transport = CookieTransport(
    cookie_name="access_token",
    cookie_max_age=60 * 60 * 24,
    cookie_path="/",
    cookie_secure=False,
    cookie_httponly=True,
    cookie_samesite="none",
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600)


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
    if not hasattr(user, "level") or user.level > 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado. Se requiere nivel de Administrador o Supervisor.",
        )
    return user
