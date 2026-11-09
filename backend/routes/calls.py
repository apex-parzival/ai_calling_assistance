"""Staff call list / detail / update. Every read and write of a patient record is audited."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import audit
from backend.db import get_session
from backend.models import Call, CallStatus, StaffUser, Urgency
from backend.routes.serialize import call_detail, call_summary
from backend.schemas import CallDetailOut, CallList, CallPatch, CallSummaryOut, StaffOut
from backend.security import current_staff

router = APIRouter(tags=["calls"])


@router.get("/calls", response_model=CallList)
async def list_calls(
    request: Request,
    status: CallStatus | None = None,
    urgent: bool = False,
    today: bool = False,
    q: str | None = Query(default=None, max_length=100),
    before: dt.datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: StaffUser = Depends(current_staff),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Call).order_by(Call.started_at.desc()).limit(limit + 1)
    if status:
        stmt = stmt.where(Call.status == status)
    if urgent:
        stmt = stmt.where(Call.urgency.in_([Urgency.urgent, Urgency.emergency]))
    if today:
        tz = request.app.state.clinic.tz()
        start = dt.datetime.combine(request.app.state.clock().astimezone(tz).date(), dt.time.min, tzinfo=tz)
        stmt = stmt.where(Call.started_at >= start)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Call.caller_name.ilike(like), Call.caller_phone.ilike(like), Call.reason.ilike(like)))
    if before:
        stmt = stmt.where(Call.started_at < before)

    rows = list((await session.scalars(stmt)).unique())
    page, more = rows[:limit], len(rows) > limit
    audit.record(session, actor=user, action="list", entity="call", request=request,
                 details={"ids": [str(c.id) for c in page]})
    await session.commit()
    return CallList(items=[CallSummaryOut(**call_summary(c)) for c in page],
                    next_before=page[-1].started_at if more else None)


@router.get("/calls/{call_id}", response_model=CallDetailOut)
async def get_call(call_id: uuid.UUID, request: Request, user: StaffUser = Depends(current_staff),
                   session: AsyncSession = Depends(get_session)):
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    audit.record(session, actor=user, action="read", entity="call", entity_id=call.id, request=request)
    await session.commit()
    return call_detail(call)


@router.patch("/calls/{call_id}", response_model=CallDetailOut)
async def update_call(call_id: uuid.UUID, body: CallPatch, request: Request,
                      user: StaffUser = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    changes: dict = {}
    fields = body.model_fields_set
    if "status" in fields and body.status is not None and body.status != call.status:
        changes["status"] = [call.status.value, body.status.value]
        call.status = body.status
    if "notes" in fields and body.notes != call.notes:
        changes["notes"] = "changed"  # don't copy note text into the audit log
        call.notes = body.notes
    if "assigned_to_id" in fields and body.assigned_to_id != call.assigned_to_id:
        if body.assigned_to_id is not None and await session.get(StaffUser, body.assigned_to_id) is None:
            raise HTTPException(422, "assigned_to_id is not a staff user")
        changes["assigned_to_id"] = [str(call.assigned_to_id) if call.assigned_to_id else None,
                                     str(body.assigned_to_id) if body.assigned_to_id else None]
        call.assigned_to_id = body.assigned_to_id
    if changes:
        audit.record(session, actor=user, action="update", entity="call", entity_id=call.id, details=changes,
                     request=request)
        await session.commit()
        await session.refresh(call, ["assigned_to"])
        await request.app.state.hub.broadcast("call.updated", call_summary(call))
    return call_detail(call)


@router.get("/staff", response_model=list[StaffOut])
async def list_staff(user: StaffUser = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(StaffUser).where(StaffUser.is_active).order_by(StaffUser.name))
    return list(rows)
