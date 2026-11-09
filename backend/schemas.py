"""Request/response models for the HTTP API."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models import BookingStatus, CallStatus, MessageStatus, StaffRole, Urgency
from core import tool_schemas


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- staff ------------------------------------------------------------------


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class StaffOut(_Out):
    id: uuid.UUID
    name: str
    email: str
    role: StaffRole


class AppointmentOut(_Out):
    id: uuid.UUID
    call_id: uuid.UUID | None
    kind: str
    patient_name: str | None
    patient_phone: str
    requested_date: dt.date
    requested_time: dt.time | None
    service: str | None
    booking_status: BookingStatus
    created_at: dt.datetime


class AppointmentPatch(BaseModel):
    booking_status: BookingStatus


class MessageOut(_Out):
    id: uuid.UUID
    call_id: uuid.UUID | None
    caller_name: str | None
    caller_phone: str | None
    reason: str
    urgency: Urgency
    status: MessageStatus
    created_at: dt.datetime


class MessagePatch(BaseModel):
    status: MessageStatus


class CallSummaryOut(_Out):
    id: uuid.UUID
    caller_name: str | None
    caller_phone: str | None
    started_at: dt.datetime
    ended_at: dt.datetime | None
    duration: int | None
    reason: str | None
    urgency: Urgency
    status: CallStatus
    outcome: str | None
    summary: str | None
    assigned_to: StaffOut | None
    appointment_requests: list[AppointmentOut] = []


class TranscriptTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    text: str
    at: dt.datetime | None = None


class LatencySample(BaseModel):
    model_config = ConfigDict(extra="allow")
    turn: int
    total: float | None = None


class CallDetailOut(CallSummaryOut):
    room_name: str | None
    transcript: list[TranscriptTurn] | None
    notes: str | None
    recording_url: str | None
    latency: list[LatencySample] | None
    messages: list[MessageOut] = []


class CallPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CallStatus | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    assigned_to_id: uuid.UUID | None = None  # send null to unassign


class CallList(BaseModel):
    items: list[CallSummaryOut]
    next_before: dt.datetime | None  # pass as ?before= for the next page


# --- agent ------------------------------------------------------------------


class CallStartIn(BaseModel):
    # Chosen by the agent so writes can be spooled and replayed if the backend is down. Idempotent.
    id: uuid.UUID | None = None
    room_name: str = Field(max_length=128)
    caller_phone: str | None = Field(default=None, max_length=32)
    started_at: dt.datetime | None = None


class IdOut(BaseModel):
    id: uuid.UUID


class CallEndIn(BaseModel):
    ended_at: dt.datetime | None = None
    caller_name: str | None = Field(default=None, max_length=120)
    caller_phone: str | None = Field(default=None, max_length=32)
    reason: str | None = Field(default=None, max_length=255)
    urgency: Urgency = Urgency.routine
    outcome: str | None = Field(default=None, max_length=64)
    summary: str | None = None
    transcript: list[TranscriptTurn] = []
    latency: list[LatencySample] = []


class CallFlagIn(BaseModel):
    urgency: Urgency
    reason: str | None = Field(default=None, max_length=255)


class CallSummaryIn(BaseModel):
    summary: str = Field(max_length=4000)
    reason: str | None = Field(default=None, max_length=255)


class AvailabilityIn(tool_schemas.CheckAvailability):
    call_id: uuid.UUID | None = None


class AvailabilityOut(BaseModel):
    date: dt.date
    service: str
    service_name: str
    slots: list[str]  # "HH:MM"
    requires_confirmation: bool
    unavailable_reason: str | None


class BookIn(tool_schemas.BookAppointment):
    call_id: uuid.UUID | None = None


class BookOut(BaseModel):
    ok: bool
    request_id: uuid.UUID | None = None
    requires_confirmation: bool = True
    error: str | None = None
    alternatives: list[str] = []


class CancelIn(tool_schemas.CancelAppointment):
    call_id: uuid.UUID | None = None


class CancelOut(BaseModel):
    ok: bool
    matched: int
    requires_confirmation: bool
    error: str | None = None


class MessageIn(tool_schemas.TakeMessage):
    call_id: uuid.UUID | None = None
    urgency: Urgency = Urgency.routine


class TransferIn(BaseModel):
    call_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=255)
    urgency: Urgency = Urgency.routine


class TransferOut(BaseModel):
    number: str
    announcement: str
