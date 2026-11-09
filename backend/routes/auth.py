from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import audit
from backend.db import get_session
from backend.models import StaffUser
from backend.schemas import LoginIn, StaffOut
from backend.security import COOKIE_NAME, cookie_kwargs, current_staff, make_session_cookie, verify_password_or_dummy

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=StaffOut)
async def login(body: LoginIn, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(StaffUser).where(func.lower(StaffUser.email) == body.email.strip().lower()))
    if not verify_password_or_dummy(body.password, user):
        audit.record(session, actor="system", action="login_failed", entity="staff_user",
                     details={"email": body.email.strip().lower()[:254]}, request=request)
        await session.commit()
        raise HTTPException(401, "wrong email or password")
    settings = request.app.state.settings
    response.set_cookie(value=make_session_cookie(settings, user), **cookie_kwargs(settings))
    audit.record(session, actor=user, action="login", entity="staff_user", entity_id=user.id, request=request)
    await session.commit()
    return user


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, user: StaffUser = Depends(current_staff),
                 session: AsyncSession = Depends(get_session)):
    response.delete_cookie(COOKIE_NAME, path="/")
    audit.record(session, actor=user, action="logout", entity="staff_user", entity_id=user.id, request=request)
    await session.commit()


@router.get("/me", response_model=StaffOut)
async def me(user: StaffUser = Depends(current_staff)):
    return user
