"""Signed, time-limited access tokens.

A token is a JWT (HS256) carrying the user id, role and expiry, signed with
``AUTH_TOKEN_SECRET``. It holds nothing secret: the signature is what makes it
trustworthy. Built on the standard library, so there is no crypto dependency to patch.
"""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import Settings, settings


class InvalidTokenError(Exception):
    """The token was missing, malformed, altered or past its expiry."""


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    role: str
    expires_at: datetime


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _signing_key(config: Settings) -> bytes:
    if config.auth_token_secret is None:
        raise RuntimeError("AUTH_TOKEN_SECRET is not set, so tokens cannot be signed.")
    return config.auth_token_secret.get_secret_value().encode()


def _sign(message: bytes, key: bytes) -> str:
    return _b64encode(hmac.new(key, message, hashlib.sha256).digest())


def _json_segment(data: dict) -> str:
    return _b64encode(json.dumps(data, separators=(",", ":")).encode())


def create_access_token(
    user_id: int, role: str, *, config: Settings | None = None, now: datetime | None = None
) -> str:
    """Issue a token that expires ``access_token_expire_minutes`` after ``now``."""
    config = config or settings
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=config.access_token_expire_minutes)

    header = _json_segment({"alg": "HS256", "typ": "JWT"})
    payload = _json_segment(
        {
            "sub": str(user_id),
            "role": role,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
    )
    signature = _sign(f"{header}.{payload}".encode(), _signing_key(config))
    return f"{header}.{payload}.{signature}"


def decode_access_token(
    token: str, *, config: Settings | None = None, now: datetime | None = None
) -> TokenClaims:
    """Verify the signature and expiry, then return the claims.

    Runs on every request that presents a token, so a token past its expiry is
    refused by the server even if the client keeps sending it.
    """
    config = config or settings
    try:
        header, payload, signature = token.split(".")
    except ValueError as exc:
        raise InvalidTokenError("Token is malformed") from exc

    expected = _sign(f"{header}.{payload}".encode(), _signing_key(config))
    if not hmac.compare_digest(signature, expected):
        raise InvalidTokenError("Token signature does not match")

    try:
        claims = json.loads(_b64decode(payload))
        user_id = int(claims["sub"])
        role = str(claims["role"])
        expires_at = datetime.fromtimestamp(claims["exp"], tz=UTC)
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidTokenError("Token claims are unreadable") from exc

    if (now or datetime.now(UTC)) >= expires_at:
        raise InvalidTokenError("Token has expired")

    return TokenClaims(user_id=user_id, role=role, expires_at=expires_at)
