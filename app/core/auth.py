import os
import uuid
from typing import Optional
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import select
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)

from app.models.user import User
from app.core.db import get_user_db

SECRET = os.getenv("SECRET")


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    async def on_afer_register(self, user: User, request: Optional[Request] = None):
        print(f"Usuario {user.id} se ha registrado exitosamente.")

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


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
