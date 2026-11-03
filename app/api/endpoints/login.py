from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import add_audit_entry
from app.core.auth import auth_backend, get_jwt_strategy, get_user_manager
from app.core.db import get_async_session
from app.core.mfa import decrypt_totp_secret, verify_totp_secret
from app.core.auth import UserManager
from app.models.user import User

router = APIRouter()


@router.post("/login", name="auth:login-with-mfa")
async def login_with_optional_mfa(
    credentials: OAuth2PasswordRequestForm = Depends(),
    totp_code: Optional[str] = Form(default=None),
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    user = await user_manager.authenticate(credentials)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Credenciales incorrectas.")

    recovery_code_used = False
    if user.mfa_enabled:
        if not totp_code:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MFA_REQUIRED")
        try:
            secret = decrypt_totp_secret(user.mfa_secret_encrypted or "")
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MFA_INVALID") from exc
        if not verify_totp_secret(secret, totp_code):
            remaining_codes = list(user.mfa_recovery_codes or [])
            password_helper = user_manager.password_helper
            matching_index = next((index for index, hashed_code in enumerate(remaining_codes) if password_helper.verify(totp_code.strip().upper(), hashed_code)), None)
            if matching_index is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MFA_INVALID")
            remaining_codes.pop(matching_index)
            user.mfa_recovery_codes = remaining_codes
            recovery_code_used = True

    user.last_login_at = datetime.utcnow()
    session.add(user)
    add_audit_entry(session, actor=user, action="auth.login", entity_type="user", entity_id=str(user.id), details={"recovery_code_used": recovery_code_used})
    await session.commit()
    return await auth_backend.login(get_jwt_strategy(), user)