import base64
import hashlib

import pyotp
from cryptography.fernet import Fernet

from app.core.auth import SECRET


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_totp_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_totp_secret(encrypted_secret: str) -> str:
    return _fernet().decrypt(encrypted_secret.encode("ascii")).decode("utf-8")


def verify_totp_secret(secret: str, code: str) -> bool:
    normalized_code = "".join(character for character in code if character.isdigit())
    return len(normalized_code) == 6 and pyotp.TOTP(secret).verify(normalized_code, valid_window=1)