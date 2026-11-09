"""Endpoints the voice agent calls. Bearer-token auth (AGENT_API_TOKEN).

The agent never touches the database or the booking system directly — every
tool goes through here.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend import audit
from backend.booking.base import get_provider
from backend.db import get_session
from backend.models import Call, Message, Urgency
from backend.notify import Notice
from backend.routes.serialize import call_summary
from backend.schemas import (
    AvailabilityIn, AvailabilityOut, BookIn, BookOut, CallEndIn, CallFlagIn, CallStartIn, CallSummaryIn, CancelIn,
    CancelOut, IdOut, MessageIn, TransferIn, TransferOut,
)
from backend.security import require_agent
from core import phone as phone_utils
from core.tool_schemas import resolve_service

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(require_agent)])


def _service_or_422(request: Request, text: str):
    service = resolve_service(request.app.state.clinic, text)
    if service is None:
        names = ", ".join(s.name for s in request.app.state.clinic.services if s.bookable)
        raise HTTPException(422, f"unknown service {text!r}; bookable services are: {names}")
    return service


async def _call_or_none(session: AsyncSession, call_id: uuid.UUID | None) -> Call | None:
    return await session.get(Call, call_id) if call_id else None


# When a call does several things, keep the most significant outcome and the highest urgency.
OUTCOME_RANK = ["", "answered", "message", "cancel_requested", "booking_requested", "transferred", "emergency"]
URGENCY_ORDER = [Urgency.routine, Urgency.urgent, Urgency.emergency]


def _set_outcome(call: Call | None, outcome: str) -> None:
    if call is None:
        return
    current = call.outcome if call.outcome in OUTCOME_RANK else ""
    if outcome not in OUTCOME_RANK:
        call.outcome = call.outcome or outcome
    elif OUTCOME_RANK.index(outcome) > OUTCOME_RANK.index(current):
        call.outcome = outcome


def _raise_urgency(call: Call, urgency: Urgency) -> None:
    call.urgency = max(call.urgency or Urgency.routine, urgency, key=URGENCY_ORDER.index)


# --- call lifecycle ---------------------------------------------------------


@router.post("/calls", response_model=IdOut, status_code=201)
async def start_call(body: CallStartIn, request: Request, session: AsyncSession = Depends(get_session)):
    if body.id and await session.get(Call, body.id):
        return IdOut(id=body.id)  # replayed or retried
    call = Call(id=body.id or uuid.uuid4(), room_name=body.room_name, caller_phone=phone_utils.normalize(body.caller_phone) or body.caller_phone,
                started_at=body.started_at or request.app.state.clock())
    session.add(call)
    await session.flush()
    audit.record(session, actor="agent", action="create", entity="call", entity_id=call.id)
    await session.commit()
    await request.app.state.hub.broadcast("call.started", call_summary(call))
    return IdOut(id=call.id)


@router.post("/calls/{call_id}/end", response_model=IdOut)
async def end_call(call_id: uuid.UUID, body: CallEndIn, request: Request,
                   session: AsyncSession = Depends(get_session)):
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    call.ended_at = body.ended_at or request.app.state.clock()
    started = call.started_at if call.started_at.tzinfo else call.started_at.replace(tzinfo=dt.timezone.utc)
    call.duration = max(0, int((call.ended_at - started).total_seconds()))
    call.caller_name = body.caller_name or call.caller_name
    call.caller_phone = phone_utils.normalize(body.caller_phone) or body.caller_phone or call.caller_phone
    call.reason = body.reason or call.reason
    _raise_urgency(call, body.urgency)  # never downgrade what was set during the call
    _set_outcome(call, body.outcome or "answered")
    call.summary = body.summary or call.summary
    call.transcript = [t.model_dump(mode="json") for t in body.transcript]
    call.latency = [s.model_dump(mode="json") for s in body.latency]
    audit.record(session, actor="agent", action="update", entity="call", entity_id=call.id,
                 details={"event": "ended", "urgency": call.urgency.value, "outcome": call.outcome})
    await session.commit()
    await session.refresh(call)

    if call.urgency != Urgency.routine:
        clinic = request.app.state.clinic
        request.app.state.notifier.send_later(Notice(
            subject=f"{call.urgency.value.title()} call from {call.caller_name or phone_utils.display(call.caller_phone)}",
            body=(f"Reason: {call.reason or 'not given'}\n"
                  f"Callback: {phone_utils.display(call.caller_phone)}\n"
                  f"Open the {clinic.name} dashboard for the summary and transcript."),
            urgent=True,
        ))
    await request.app.state.hub.broadcast("call.ended", call_summary(call))
    return IdOut(id=call.id)


@router.post("/calls/{call_id}/flag", response_model=IdOut)
async def flag_call(call_id: uuid.UUID, body: CallFlagIn, request: Request,
                    session: AsyncSession = Depends(get_session)):
    """Raise urgency mid-call (emergency detected) so staff are alerted now, not after hang-up."""
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    before = call.urgency
    _raise_urgency(call, body.urgency)
    if body.urgency == Urgency.emergency:
        _set_outcome(call, "emergency")
    call.reason = call.reason or body.reason
    audit.record(session, actor="agent", action="update", entity="call", entity_id=call.id,
                 details={"event": "flag", "urgency": call.urgency.value})
    await session.commit()
    if call.urgency != before:
        request.app.state.notifier.send_later(Notice(
            subject=f"{call.urgency.value.title()} — live call in progress",
            body=(f"The AI receptionist detected {call.urgency.value} language on a live call"
                  f"{' from ' + phone_utils.display(call.caller_phone) if call.caller_phone else ''}: "
                  f"{body.reason or 'see dashboard'}. The caller was told to hang up and call 9-1-1."
                  if call.urgency == Urgency.emergency else
                  f"Urgent call in progress: {body.reason or 'see dashboard'}."),
            urgent=True,
        ))
        await request.app.state.hub.broadcast("call.updated", call_summary(call))
    return IdOut(id=call.id)


@router.post("/calls/{call_id}/summary", response_model=IdOut)
async def set_summary(call_id: uuid.UUID, body: CallSummaryIn, request: Request,
                      session: AsyncSession = Depends(get_session)):
    """The agent posts the LLM summary after hang-up, separately, so the call shows up immediately."""
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    call.summary = body.summary
    call.reason = body.reason or call.reason
    audit.record(session, actor="agent", action="update", entity="call", entity_id=call.id,
                 details={"event": "summary"})
    await session.commit()
    await session.refresh(call)
    await request.app.state.hub.broadcast("call.updated", call_summary(call))
    return IdOut(id=call.id)


# --- tools ------------------------------------------------------------------


@router.post("/availability", response_model=AvailabilityOut)
async def check_availability(body: AvailabilityIn, request: Request, session: AsyncSession = Depends(get_session)):
    service = _service_or_422(request, body.service)
    provider = get_provider(request.app.state.clinic, session, request.app.state.notifier)
    a = await provider.check_availability(body.date, service.id, request.app.state.clock())
    return AvailabilityOut(date=a.date, service=service.id, service_name=service.name,
                           slots=[t.strftime("%H:%M") for t in a.slots],
                           requires_confirmation=a.requires_confirmation, unavailable_reason=a.unavailable_reason)


@router.post("/appointments", response_model=BookOut)
async def book(body: BookIn, request: Request, session: AsyncSession = Depends(get_session)):
    service = _service_or_422(request, body.service)
    call = await _call_or_none(session, body.call_id)
    provider = get_provider(request.app.state.clinic, session, request.app.state.notifier)
    r = await provider.create_request(call_id=body.call_id, name=body.name, phone=body.phone, date=body.date,
                                      time=body.time, service_id=service.id, now=request.app.state.clock())
    if r.ok and call is not None:
        call.caller_name = call.caller_name or body.name
        call.caller_phone = call.caller_phone or body.phone
        call.reason = call.reason or f"Book: {service.name}"
        _set_outcome(call, "booking_requested")
        await session.commit()
    return BookOut(ok=r.ok, request_id=r.request_id, requires_confirmation=r.requires_confirmation, error=r.error,
                   alternatives=[t.strftime("%H:%M") for t in r.alternatives])


@router.post("/appointments/cancel", response_model=CancelOut)
async def cancel(body: CancelIn, request: Request, session: AsyncSession = Depends(get_session)):
    provider = get_provider(request.app.state.clinic, session, request.app.state.notifier)
    r = await provider.cancel_request(call_id=body.call_id, phone=body.phone, date=body.date)
    call = await _call_or_none(session, body.call_id)
    if r.ok and call is not None:
        call.caller_phone = call.caller_phone or body.phone
        call.reason = call.reason or "Cancel appointment"
        _set_outcome(call, "cancel_requested")
        await session.commit()
    return CancelOut(ok=r.ok, matched=r.matched, requires_confirmation=r.requires_confirmation, error=r.error)


@router.post("/messages", response_model=IdOut, status_code=201)
async def take_message(body: MessageIn, request: Request, session: AsyncSession = Depends(get_session)):
    msg = Message(call_id=body.call_id, caller_name=body.name, caller_phone=body.phone, reason=body.reason,
                  urgency=body.urgency)
    session.add(msg)
    call = await _call_or_none(session, body.call_id)
    if call is not None:
        call.caller_name = call.caller_name or body.name
        call.caller_phone = call.caller_phone or body.phone
        call.reason = call.reason or body.reason[:255]
        _set_outcome(call, "message")
    await session.flush()
    audit.record(session, actor="agent", action="create", entity="message", entity_id=msg.id,
                 details={"call_id": str(body.call_id) if body.call_id else None})
    await session.commit()
    request.app.state.notifier.send_later(Notice(
        subject=f"Message from {body.name}",
        body=f"{body.name} ({phone_utils.display(body.phone)}): {body.reason}",
        urgent=body.urgency != Urgency.routine,
    ))
    return IdOut(id=msg.id)


@router.post("/transfer", response_model=TransferOut)
async def transfer(body: TransferIn, request: Request, session: AsyncSession = Depends(get_session)):
    """Records the transfer and returns where to send the caller. The agent performs the SIP transfer."""
    clinic = request.app.state.clinic
    call = await _call_or_none(session, body.call_id)
    if call is not None:
        call.reason = call.reason or body.reason or "Transferred to staff"
        _raise_urgency(call, body.urgency)
        _set_outcome(call, "transferred")
        audit.record(session, actor="agent", action="update", entity="call", entity_id=call.id,
                     details={"event": "transfer", "reason": body.reason})
        await session.commit()
    return TransferOut(number=clinic.escalation.transfer_number, announcement=clinic.escalation.transfer_announcement)
