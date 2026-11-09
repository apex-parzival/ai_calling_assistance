"""Default provider: no booking system API. Requests are recorded and the front desk
confirms them by phone, then marks them confirmed in the dashboard.

Availability is computed from clinic hours, minimum notice and the booking window,
minus times already requested — it is a best guess, which is why every result
`requires_confirmation`.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select

from backend import audit
from backend.booking.base import Availability, BookingProvider, BookingResult, CancelResult
from backend.models import AppointmentRequest, BookingStatus, RequestKind
from backend.notify import Notice
from core import phone as phone_utils
from core.tool_schemas import check_date_window

SLOT_STEP_MINUTES = 15
HELD = (BookingStatus.requested, BookingStatus.confirmed)


class ManualBookingProvider(BookingProvider):
    async def check_availability(self, date: dt.date, service_id: str, now: dt.datetime) -> Availability:
        result = Availability(date=date, service=service_id)
        service = self.clinic.service(service_id)
        if service is None:
            result.unavailable_reason = f"unknown service {service_id!r}"
            return result
        if not service.bookable:
            result.unavailable_reason = f"{service.name} can't be booked by phone. {service.notes}".strip()
            return result

        local_now = now.astimezone(self.clinic.tz())
        if err := check_date_window(self.clinic, date, local_now.date()):
            result.unavailable_reason = err
            return result
        ranges = self.clinic.hours_on(date)
        if not ranges:
            result.unavailable_reason = f"the clinic is closed on {date.strftime('%A, %B')} {date.day}"
            return result

        earliest = local_now + dt.timedelta(hours=self.clinic.booking.min_notice_hours)
        taken = await self._taken_times(date)
        duration = dt.timedelta(minutes=service.duration_minutes)
        step = dt.timedelta(minutes=SLOT_STEP_MINUTES)
        for r in ranges:
            start = dt.datetime.combine(date, r.open, tzinfo=self.clinic.tz())
            end = dt.datetime.combine(date, r.close, tzinfo=self.clinic.tz())
            t = start
            while t + duration <= end:
                if t >= earliest and t.time() not in taken:
                    result.slots.append(t.time())
                t += step
        if not result.slots:
            result.unavailable_reason = "no open times left that day"
        return result

    async def _taken_times(self, date: dt.date) -> set[dt.time]:
        rows = await self.session.scalars(
            select(AppointmentRequest.requested_time).where(
                AppointmentRequest.requested_date == date,
                AppointmentRequest.kind == RequestKind.book,
                AppointmentRequest.booking_status.in_(HELD),
            )
        )
        return {t for t in rows if t is not None}

    async def create_request(self, *, call_id: uuid.UUID | None, name: str, phone: str, date: dt.date,
                             time: dt.time, service_id: str, now: dt.datetime) -> BookingResult:
        availability = await self.check_availability(date, service_id, now)
        if availability.unavailable_reason:
            return BookingResult(ok=False, error=availability.unavailable_reason)
        if time not in availability.slots:
            nearest = sorted(availability.slots, key=lambda s: abs(_minutes(s) - _minutes(time)))[:3]
            return BookingResult(ok=False, error=f"{time.strftime('%H:%M')} is not available",
                                 alternatives=sorted(nearest))

        req = AppointmentRequest(
            call_id=call_id, kind=RequestKind.book, patient_name=name, patient_phone=phone,
            requested_date=date, requested_time=time, service=service_id,
            booking_status=BookingStatus.requested,
        )
        self.session.add(req)
        await self.session.flush()
        audit.record(self.session, actor="agent", action="create", entity="appointment_request", entity_id=req.id,
                     details={"call_id": str(call_id) if call_id else None, "kind": "book"})
        await self.session.commit()

        service = self.clinic.service(service_id)
        self.notifier.send_later(Notice(
            subject=f"Appointment request: {name}, {date:%a %b} {date.day} {time:%H:%M}",
            body=(f"{name} ({phone_utils.display(phone)}) asked for a {service.name} on "
                  f"{date:%A %B} {date.day} at {time:%H:%M}.\n"
                  f"Please book it and call to confirm, then mark it confirmed in the dashboard."),
        ))
        return BookingResult(ok=True, request_id=req.id)

    async def cancel_request(self, *, call_id: uuid.UUID | None, phone: str, date: dt.date) -> CancelResult:
        existing = list(await self.session.scalars(
            select(AppointmentRequest).where(
                AppointmentRequest.patient_phone == phone,
                AppointmentRequest.requested_date == date,
                AppointmentRequest.kind == RequestKind.book,
                AppointmentRequest.booking_status.in_(HELD),
            )
        ))
        for req in existing:
            req.booking_status = BookingStatus.cancel_requested
            audit.record(self.session, actor="agent", action="update", entity="appointment_request",
                         entity_id=req.id, details={"booking_status": "cancel_requested", "call_id": str(call_id)})
        if not existing:
            # Booked before this system existed (or by another channel): record the cancellation for staff.
            req = AppointmentRequest(call_id=call_id, kind=RequestKind.cancel, patient_phone=phone,
                                     requested_date=date, booking_status=BookingStatus.cancel_requested)
            self.session.add(req)
            await self.session.flush()
            audit.record(self.session, actor="agent", action="create", entity="appointment_request",
                         entity_id=req.id, details={"call_id": str(call_id) if call_id else None, "kind": "cancel"})
        await self.session.commit()

        self.notifier.send_later(Notice(
            subject=f"Cancellation request for {date:%a %b} {date.day}",
            body=(f"The caller at {phone_utils.display(phone)} asked to cancel their appointment on {date:%A %B} {date.day}. "
                  f"{'Matched ' + str(len(existing)) + ' request(s) in the dashboard.' if existing else 'No matching request in the dashboard — check the booking system.'}\n"
                  f"Please cancel it in the booking system and mark it cancelled in the dashboard."),
        ))
        return CancelResult(ok=True, matched=len(existing))


def _minutes(t: dt.time) -> int:
    return t.hour * 60 + t.minute
