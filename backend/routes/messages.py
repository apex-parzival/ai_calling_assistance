from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import audit
from backend.db import get_session
from backend.models import Message, MessageStatus, StaffUser
from backend.schemas import MessageOut, MessagePatch
from backend.security import current_staff

router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("", response_model=list[MessageOut])
async def list_messages(request: Request, status: MessageStatus | None = None,
                        user: StaffUser = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    stmt = select(Message).order_by(Message.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(Message.status == status)
    rows = list(await session.scalars(stmt))
    audit.record(session, actor=user, action="list", entity="message", request=request,
                 details={"ids": [str(m.id) for m in rows]})
    await session.commit()
    return rows


@router.patch("/{message_id}", response_model=MessageOut)
async def update_message(message_id: uuid.UUID, body: MessagePatch, request: Request,
                         user: StaffUser = Depends(current_staff), session: AsyncSession = Depends(get_session)):
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, "message not found")
    if body.status != msg.status:
        audit.record(session, actor=user, action="update", entity="message", entity_id=msg.id,
                     details={"status": [msg.status.value, body.status.value]}, request=request)
        msg.status = body.status
        await session.commit()
    return msg
