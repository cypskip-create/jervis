"""Local account authentication and short-lived HMAC bearer tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import User


class AuthenticationError(ValueError):
    """Credentials, token or signing configuration are invalid."""


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    username: str
    role: str


def create_user(session: Session, username: str, password: str, role: str = "admin") -> User:
    normalized = username.strip().lower()
    if not normalized or len(normalized) > 120:
        raise ValueError("username must contain 1 to 120 characters")
    if len(password) < 14 or len(password) > 1024:
        raise ValueError("password must contain at least 14 characters")
    if role not in {"admin", "operator", "viewer"}:
        raise ValueError("unsupported user role")
    if session.scalar(select(User.id).where(User.username == normalized)):
        raise ValueError("username already exists")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
    )
    record = User(
        username=normalized, password_hash=f"scrypt${_b64(salt)}${_b64(digest)}", role=role
    )
    session.add(record)
    session.flush()
    return record


def authenticate(session: Session, username: str, password: str) -> Principal:
    normalized = username.strip().lower()
    record = session.scalar(select(User).where(User.username == normalized, User.enabled.is_(True)))
    encoded = (
        record.password_hash
        if record
        else "scrypt$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
    try:
        scheme, salt_text, expected_text = encoded.split("$", 2)
        if scheme != "scrypt":
            raise ValueError
        actual = hashlib.scrypt(
            password.encode(),
            salt=_unb64(salt_text),
            n=2**15,
            r=8,
            p=1,
            dklen=32,
            maxmem=64 * 1024 * 1024,
        )
        valid = hmac.compare_digest(actual, _unb64(expected_text))
    except (ValueError, TypeError):
        valid = False
    if not valid or record is None:
        raise AuthenticationError("invalid username or password")
    return Principal(record.id, record.username, record.role)


def issue_token(principal: Principal, *, lifetime_seconds: int = 8 * 60 * 60) -> str:
    if not 60 <= lifetime_seconds <= 24 * 60 * 60:
        raise ValueError("token lifetime must be between 60 seconds and 24 hours")
    payload = {
        "sub": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "exp": int(time.time()) + lifetime_seconds,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def verify_token(token: str) -> Principal:
    try:
        body, signature_text = token.split(".", 1)
        signature = _unb64(signature_text)
        expected = hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(_unb64(body))
        if int(payload["exp"]) <= int(time.time()):
            raise ValueError
        if payload["role"] not in {"admin", "operator", "viewer"}:
            raise ValueError
        return Principal(str(payload["sub"]), str(payload["username"]), str(payload["role"]))
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise AuthenticationError("invalid or expired bearer token") from exc


def _signing_key() -> bytes:
    value = os.environ.get("TRADING_PLATFORM_AUTH_SIGNING_KEY", "")
    if len(value.encode()) < 32:
        raise AuthenticationError("API signing key is not configured (minimum 32 bytes)")
    return value.encode()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
