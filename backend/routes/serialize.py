"""Model -> JSON helpers shared by routes and WebSocket broadcasts."""

from __future__ import annotations

from sqlalchemy import inspect

from backend.models import Call
from backend.schemas import AppointmentOut, CallDetailOut, CallSummaryOut, MessageOut, StaffOut


def _loaded(obj, attr: str, default=None):
    # Reading an unloaded relationship would trigger lazy IO, which async sessions forbid.
    return default if attr in inspect(obj).unloaded else getattr(obj, attr)


def _summary_fields(call: Call) -> dict:
    assigned = _loaded(call, "assigned_to")
    return dict(
        id=call.id, caller_name=call.caller_name, caller_phone=call.caller_phone, started_at=call.started_at,
        ended_at=call.ended_at, duration=call.duration, reason=call.reason, urgency=call.urgency,
        status=call.status, outcome=call.outcome, summary=call.summary,
        assigned_to=StaffOut.model_validate(assigned) if assigned else None,
        appointment_requests=[AppointmentOut.model_validate(a) for a in _loaded(call, "appointment_requests", [])],
    )


def call_summary(call: Call) -> dict:
    return CallSummaryOut(**_summary_fields(call)).model_dump(mode="json")


def call_detail(call: Call) -> CallDetailOut:
    return CallDetailOut(
        **_summary_fields(call),
        room_name=call.room_name, transcript=call.transcript, notes=call.notes, recording_url=call.recording_url,
        latency=call.latency,
        messages=[MessageOut.model_validate(m) for m in _loaded(call, "messages", [])],
    )
