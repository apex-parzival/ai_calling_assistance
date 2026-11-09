"""Appointment requests: staff confirm/decline/cancel them after acting in the real booking system."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import audit
from backend.db import get_session
from backend.models import AppointmentRequest, BookingStatus, Call, StaffUser
from backend.routes.serialize import call_summary
from backend.schemas import AppointmentOut, AppointmentPatch
from backend.security import current_staff

router = APIRouter(prefix="/appointments", tags=["appointments"])


@router.get("", response_model=list[AppointmentOut])
async def list_requests(request: Request, booking_status: BookingStatus | None = None,
                        user: StaffUser = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    stmt = select(AppointmentRequest).order_by(AppointmentRequest.created_at.desc()).limit(200)
    if booking_status:
        stmt = stmt.where(AppointmentRequest.booking_status == booking_status)
    rows = list(await session.scalars(stmt))
    audit.record(session, actor=user, action="list", entity="appointment_request", request=request,
                 details={"ids": [str(r.id) for r in rows]})
    await session.commit()
    return rows


@router.patch("/{request_id}", response_model=AppointmentOut)
async def update_request(request_id: uuid.UUID, body: AppointmentPatch, request: Request,
                         user: StaffUser = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    req = await session.get(AppointmentRequest, request_id)
    if req is None:
        raise HTTPException(404, "appointment request not found")
    if body.booking_status != req.booking_status:
        audit.record(session, actor=user, action="update", entity="appointment_request", entity_id=req.id,
                     details={"booking_status": [req.booking_status.value, body.booking_status.value]},
                     request=request)
        req.booking_status = body.booking_status
        await session.commit()
        if req.call_id and (call := await session.get(Call, req.call_id)):
            await session.refresh(call, ["appointment_requests"])
            await request.app.state.hub.broadcast("call.updated", call_summary(call))
    return req
