"""BookingProvider interface. clinic.yaml `booking.provider` picks the implementation."""

from __future__ import annotations

import datetime as dt
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from backend.notify import Notifier
from core.config import ClinicConfig


@dataclass
class Availability:
    date: dt.date
    service: str
    slots: list[dt.time] = field(default_factory=list)
    # True when a person still has to confirm (manual provider): the agent must say
    # "the front desk will call you to confirm", never "you're booked".
    requires_confirmation: bool = True
    unavailable_reason: str | None = None  # e.g. "the clinic is closed on Sundays"


@dataclass
class BookingResult:
    ok: bool
    request_id: uuid.UUID | None = None
    requires_confirmation: bool = True
    error: str | None = None
    alternatives: list[dt.time] = field(default_factory=list)


@dataclass
class CancelResult:
    ok: bool
    matched: int = 0  # existing requests found for that phone + date
    requires_confirmation: bool = True
    error: str | None = None


class BookingProvider(ABC):
    def __init__(self, clinic: ClinicConfig, session: AsyncSession, notifier: Notifier) -> None:
        self.clinic = clinic
        self.session = session
        self.notifier = notifier

    @abstractmethod
    async def check_availability(self, date: dt.date, service_id: str, now: dt.datetime) -> Availability: ...

    @abstractmethod
    async def create_request(self, *, call_id: uuid.UUID | None, name: str, phone: str, date: dt.date,
                             time: dt.time, service_id: str, now: dt.datetime) -> BookingResult: ...

    @abstractmethod
    async def cancel_request(self, *, call_id: uuid.UUID | None, phone: str, date: dt.date) -> CancelResult: ...


def get_provider(clinic: ClinicConfig, session: AsyncSession, notifier: Notifier) -> BookingProvider:
    if clinic.booking.provider == "manual":
        from backend.booking.manual import ManualBookingProvider
        return ManualBookingProvider(clinic, session, notifier)
    raise NotImplementedError(f"booking provider {clinic.booking.provider!r} is not implemented yet (Phase 3)")
