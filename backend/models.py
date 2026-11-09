"""SQLAlchemy models.

Types are kept portable (Uuid, JSON with a JSONB variant, non-native enums) so the
test suite can run on SQLite while every real deployment runs on PostgreSQL.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, Integer, String, Text, Time, TypeDecorator, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC in and out, whatever the database does.

    PostgreSQL keeps the offset; SQLite drops it, and a naive timestamp in the API
    would be shown in the viewer's local time instead of UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def _enum(cls: type[enum.Enum]) -> Enum:
    # Stored as VARCHAR + CHECK, using the values ("In progress"), not the names.
    return Enum(cls, native_enum=False, values_callable=lambda e: [m.value for m in e], length=32,
                validate_strings=True)


class Base(DeclarativeBase):
    pass


class CallStatus(str, enum.Enum):
    new = "New"
    in_progress = "In progress"
    handled = "Handled"


class Urgency(str, enum.Enum):
    routine = "routine"
    urgent = "urgent"
    emergency = "emergency"


class BookingStatus(str, enum.Enum):
    requested = "requested"            # agent recorded it; staff must confirm (manual provider)
    confirmed = "confirmed"
    declined = "declined"
    cancel_requested = "cancel_requested"
    cancelled = "cancelled"


class RequestKind(str, enum.Enum):
    book = "book"
    cancel = "cancel"


class MessageStatus(str, enum.Enum):
    new = "New"
    handled = "Handled"


class StaffRole(str, enum.Enum):
    admin = "admin"
    staff = "staff"


class StaffUser(Base):
    __tablename__ = "staff_users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[StaffRole] = mapped_column(_enum(StaffRole), default=StaffRole.staff)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    room_name: Mapped[str | None] = mapped_column(String(128), index=True)
    caller_name: Mapped[str | None] = mapped_column(String(120))
    caller_phone: Mapped[str | None] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration: Mapped[int | None] = mapped_column(Integer)  # seconds
    reason: Mapped[str | None] = mapped_column(String(255))
    urgency: Mapped[Urgency] = mapped_column(_enum(Urgency), default=Urgency.routine, index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    # [{"role": "user"|"assistant", "text": str, "at": iso8601}]
    transcript: Mapped[list | None] = mapped_column(JSONType)
    recording_url: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[CallStatus] = mapped_column(_enum(CallStatus), default=CallStatus.new, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id", ondelete="SET NULL"))
    # Per-turn latency samples from the agent: [{"turn": n, "eou": s, "llm_ttft": s, "tts_ttfb": s, "total": s}]
    latency: Mapped[list | None] = mapped_column(JSONType)
    outcome: Mapped[str | None] = mapped_column(String(64))  # e.g. booked, message, transferred, emergency
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    assigned_to: Mapped[StaffUser | None] = relationship(lazy="joined")
    appointment_requests: Mapped[list[AppointmentRequest]] = relationship(
        back_populates="call", order_by="AppointmentRequest.created_at", lazy="selectin")
    messages: Mapped[list[Message]] = relationship(
        back_populates="call", order_by="Message.created_at", lazy="selectin")


class AppointmentRequest(Base):
    __tablename__ = "appointment_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"), index=True)
    kind: Mapped[RequestKind] = mapped_column(_enum(RequestKind), default=RequestKind.book)
    patient_name: Mapped[str | None] = mapped_column(String(120))
    patient_phone: Mapped[str] = mapped_column(String(32), index=True)
    requested_date: Mapped[date] = mapped_column(Date, index=True)
    requested_time: Mapped[time | None] = mapped_column(Time)
    service: Mapped[str | None] = mapped_column(String(64))
    booking_status: Mapped[BookingStatus] = mapped_column(_enum(BookingStatus), default=BookingStatus.requested,
                                                          index=True)
    external_ref: Mapped[str | None] = mapped_column(String(128))  # id in the clinic's booking system
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    call: Mapped[Call | None] = relationship(back_populates="appointment_requests")


class Message(Base):
    """A message taken for the front desk (take_message tool, or a tool-failure fallback)."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"), index=True)
    caller_name: Mapped[str | None] = mapped_column(String(120))
    caller_phone: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    urgency: Mapped[Urgency] = mapped_column(_enum(Urgency), default=Urgency.routine)
    status: Mapped[MessageStatus] = mapped_column(_enum(MessageStatus), default=MessageStatus.new, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    call: Mapped[Call | None] = relationship(back_populates="messages")


class AuditLog(Base):
    """Who viewed or changed which record, and when. Append-only."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # staff | agent | system
    actor_id: Mapped[str | None] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(32))      # read | list | create | update | login | logout ...
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    details: Mapped[dict | None] = mapped_column(JSONType)
    ip: Mapped[str | None] = mapped_column(String(64))
