import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.accounts.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_TOKEN_HEADER = {"alg": "HS256", "typ": "JWT"}


def hash_password(password: str) -> str:
    """Hash a password with a unique random salt using scrypt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded_hash: str) -> bool:
    """Return false for malformed hashes and compare valid hashes in constant time."""
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded_hash.split("$")
        if algorithm != "scrypt":
            return False
        salt = _b64decode(salt_text)
        expected = _b64decode(digest_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p)
        )
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_access_token(user_id: int) -> tuple[str, int]:
    secret = _token_secret()
    lifetime = settings.access_token_expire_minutes * 60
    now = int(time.time())
    header = _b64encode(json.dumps(_TOKEN_HEADER, separators=(",", ":")).encode())
    claims = _b64encode(
        json.dumps(
            {"sub": str(user_id), "iat": now, "exp": now + lifetime},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    signature = _b64encode(hmac.new(secret, signing_input, hashlib.sha256).digest())
    return f"{header}.{claims}.{signature}", lifetime


def _decode_access_token(token: str) -> int:
    try:
        header_text, claims_text, signature_text = token.split(".")
        signing_input = f"{header_text}.{claims_text}".encode("ascii")
        expected = hmac.new(_token_secret(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(signature_text)):
            raise ValueError("Invalid signature")
        header = json.loads(_b64decode(header_text))
        claims: dict[str, Any] = json.loads(_b64decode(claims_text))
        if header != _TOKEN_HEADER or int(claims["exp"]) <= int(time.time()):
            raise ValueError("Invalid or expired token")
        return int(claims["sub"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    user_id = _decode_access_token(token)
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_roles(*roles: str) -> Callable[..., User]:
    allowed_roles = frozenset(roles)

    def role_guard(user: User = Depends(get_current_user)) -> User:
        if user.role.name not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return role_guard


def _token_secret() -> bytes:
    configured = settings.auth_token_secret
    if configured is None or len(configured.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured; set AUTH_TOKEN_SECRET to at least 32 bytes",
        )
    return configured.encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
