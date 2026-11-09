"""Password hashing, staff session cookies, and agent token auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, WebSocket, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_session
from backend.models import StaffRole, StaffUser
from core.settings import Settings

COOKIE_NAME = "clinic_session"

# scrypt parameters (n=2**14, r=8, p=1) ~ 16 MiB, OWASP's minimum recommendation.
_N, _R, _P, _DKLEN = 2**14, 8, 1, 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p),
                            dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


# Run when the email doesn't exist, so login timing doesn't reveal which emails are staff.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def verify_password_or_dummy(password: str, user: StaffUser | None) -> bool:
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    return ok and user is not None and user.is_active


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt="staff-session")


def make_session_cookie(settings: Settings, user: StaffUser) -> str:
    return _serializer(settings).dumps({"uid": str(user.id)})


def read_session_cookie(settings: Settings, value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        data = _serializer(settings).loads(value, max_age=settings.session_max_age_hours * 3600)
        return uuid.UUID(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None


def cookie_kwargs(settings: Settings) -> dict:
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": settings.environment == "production",
        "max_age": settings.session_max_age_hours * 3600,
        "path": "/",
    }


async def _staff_from_cookie(settings: Settings, cookie: str | None, session: AsyncSession) -> StaffUser | None:
    uid = read_session_cookie(settings, cookie)
    if uid is None:
        return None
    user = await session.get(StaffUser, uid)
    return user if user and user.is_active else None


async def current_staff(request: Request, session: AsyncSession = Depends(get_session)) -> StaffUser:
    user = await _staff_from_cookie(request.app.state.settings, request.cookies.get(COOKIE_NAME), session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    return user


async def require_admin(user: StaffUser = Depends(current_staff)) -> StaffUser:
    if user.role != StaffRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user


async def staff_for_websocket(ws: WebSocket) -> StaffUser | None:
    settings: Settings = ws.app.state.settings
    async with ws.app.state.sessionmaker() as session:
        return await _staff_from_cookie(settings, ws.cookies.get(COOKIE_NAME), session)


@dataclass(frozen=True)
class AgentPrincipal:
    room: str | None


async def require_agent(request: Request) -> AgentPrincipal:
    """The agent authenticates with `Authorization: Bearer <AGENT_API_TOKEN>`."""
    expected = request.app.state.settings.agent_api_token.get_secret_value()
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid agent token")
    return AgentPrincipal(room=request.headers.get("x-livekit-room"))
