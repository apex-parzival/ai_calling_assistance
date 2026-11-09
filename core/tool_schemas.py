"""Payload schemas for the five LLM tools.

The agent validates every tool call against these before acting, and the backend
validates the same shapes again on arrival. Small models get names, numbers and
dates wrong, so the validators are strict and the error text is written to be
fed back to the LLM as a correction.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from core import phone as phone_utils
from core.config import ClinicConfig, Service


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _phone(v: str) -> str:
    normalized = phone_utils.normalize(v)
    if normalized is None:
        raise ValueError("phone must be a 10-digit North American number, e.g. 403-555-0142")
    return normalized


def _name(v: str) -> str:
    v = re.sub(r"\s+", " ", v)
    if not re.fullmatch(r"[^\W\d_][\w .'\-]{0,118}", v):
        raise ValueError("name must be the caller's name in letters, e.g. 'Jordan Lee'")
    return v


Phone = Annotated[str, AfterValidator(_phone)]
Name = Annotated[str, AfterValidator(_name)]


class CheckAvailability(_Payload):
    date: dt.date = Field(description="YYYY-MM-DD")
    service: str = Field(min_length=1, max_length=64)


class BookAppointment(_Payload):
    name: Name
    phone: Phone
    date: dt.date
    time: dt.time = Field(description="HH:MM, 24-hour, clinic local time")
    service: str = Field(min_length=1, max_length=64)


class CancelAppointment(_Payload):
    phone: Phone
    date: dt.date


class TakeMessage(_Payload):
    name: Name
    phone: Phone
    reason: str = Field(min_length=3, max_length=1000)


class TransferToHuman(_Payload):
    pass


def resolve_service(clinic: ClinicConfig, text: str) -> Service | None:
    """Match a service by id, exact name, or a unique partial name match."""
    t = text.strip().lower().replace("-", " ")
    for s in clinic.services:
        if t in (s.id, s.id.replace("_", " "), s.name.lower()):
            return s
    partial = [s for s in clinic.services if t in s.name.lower() or s.name.lower() in t]
    return partial[0] if len(partial) == 1 else None


def correction_message(err: ValidationError) -> str:
    """Turn a validation error into a short instruction for the LLM's retry."""
    parts = []
    for e in err.errors():
        field = ".".join(str(p) for p in e["loc"]) or "arguments"
        parts.append(f"{field}: {e['msg'].removeprefix('Value error, ')}")
    return "The tool call was invalid — " + "; ".join(parts) + ". Ask the caller if needed, then call the tool again."


def check_date_window(clinic: ClinicConfig, day: dt.date, today: dt.date) -> str | None:
    if day < today:
        return f"{day.isoformat()} is in the past; today is {today.isoformat()}"
    if (day - today).days > clinic.booking.max_days_ahead:
        return f"bookings can only be made up to {clinic.booking.max_days_ahead} days ahead"
    return None
