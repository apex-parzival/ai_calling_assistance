"""Load and validate a clinic's `clinic.yaml`.

Everything that differs between clinics lives in that file. Validation runs at
process startup so a bad config fails loudly before any call is answered,
never mid-call.
"""

from __future__ import annotations

import re
from datetime import date, time
from enum import Enum
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
CLINICS_ROOT = REPO_ROOT / "clinics"

E164 = re.compile(r"^\+[1-9]\d{7,14}$")

# The greeting must tell the caller they are talking to an AI, and (when
# recording is on) that the call is recorded. Checked here so a clinic config
# without the disclosure can never be deployed.
AI_DISCLOSURE = re.compile(r"\b(ai|artificial intelligence|automated|virtual assistant)\b", re.IGNORECASE)
RECORDING_NOTICE = re.compile(r"\brecord", re.IGNORECASE)


class ConfigError(Exception):
    """Raised when clinic.yaml is missing or invalid. Message is human-readable."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Weekday(str, Enum):
    monday = "monday"
    tuesday = "tuesday"
    wednesday = "wednesday"
    thursday = "thursday"
    friday = "friday"
    saturday = "saturday"
    sunday = "sunday"


class TimeRange(_Strict):
    open: time
    close: time

    @model_validator(mode="after")
    def _ordered(self) -> TimeRange:
        if self.open >= self.close:
            raise ValueError(f"open ({self.open}) must be before close ({self.close})")
        return self


class Address(_Strict):
    street: str
    city: str
    province: str
    postal_code: str
    directions: str = ""

    def one_line(self) -> str:
        return f"{self.street}, {self.city}, {self.province} {self.postal_code}"


class Service(_Strict):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    duration_minutes: int = Field(gt=0, le=240)
    description: str = ""
    bookable: bool = True
    notes: str = ""


class FAQ(_Strict):
    topic: str
    question: str
    answer: str


class Escalation(_Strict):
    transfer_number: str
    transfer_announcement: str
    # Topics that must always reach a human (by transfer or message), in the clinic's words.
    always_human: list[str] = []
    # Extra clinic-specific phrases that trigger the fixed 911 response, on top of core.guardrails.
    extra_urgent_phrases: list[str] = []

    @field_validator("transfer_number")
    @classmethod
    def _e164(cls, v: str) -> str:
        if not E164.match(v):
            raise ValueError("must be E.164, e.g. +14035550123")
        return v


class Recording(_Strict):
    enabled: bool = True


class Voice(_Strict):
    tts_provider: Literal["cartesia", "elevenlabs", "kokoro"] = "cartesia"
    stt_provider: Literal["deepgram", "faster_whisper"] = "deepgram"
    # Required before the agent runs (checked by core.check_env); optional so the
    # backend can start before vendor accounts exist.
    voice_id: str | None = None
    language: str = "en-CA"
    # Seconds of silence before the caller's turn counts as finished (Silero VAD silence window
    # and LiveKit endpointing delay). This wait is part of every reply's latency. Lower answers
    # sooner but can cut in on callers who pause mid-sentence, e.g. while reading out a number.
    # 0.55 is the LiveKit/Silero default.
    end_of_turn_silence: float = Field(default=0.55, ge=0.25, le=1.5)


class Booking(_Strict):
    provider: Literal["manual", "jane", "google_calendar"] = "manual"
    # Earliest/latest days ahead a caller may request.
    min_notice_hours: int = Field(default=2, ge=0)
    max_days_ahead: int = Field(default=60, gt=0)
    # Provider-specific, non-secret options. Credentials go in the clinic's .env.
    options: dict[str, str | int | bool] = {}


class Notifications(_Strict):
    staff_emails: list[str] = []
    staff_sms: list[str] = []

    @field_validator("staff_sms")
    @classmethod
    def _e164_all(cls, v: list[str]) -> list[str]:
        bad = [n for n in v if not E164.match(n)]
        if bad:
            raise ValueError(f"numbers must be E.164: {bad}")
        return v

    @model_validator(mode="after")
    def _someone(self) -> Notifications:
        if not self.staff_emails and not self.staff_sms:
            raise ValueError("at least one staff email or SMS number is required for urgent alerts")
        return self


class Limits(_Strict):
    max_turns: int = Field(default=40, gt=0)
    max_call_seconds: int = Field(default=900, gt=0)
    silence_reprompt_seconds: float = Field(default=8, gt=0)
    silence_hangup_seconds: float = Field(default=30, gt=0)

    @model_validator(mode="after")
    def _silence_order(self) -> Limits:
        if self.silence_reprompt_seconds >= self.silence_hangup_seconds:
            raise ValueError("silence_reprompt_seconds must be less than silence_hangup_seconds")
        return self


class ClinicConfig(_Strict):
    clinic_id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    timezone: str
    main_phone: str
    website: str = ""
    address: Address
    hours: dict[Weekday, list[TimeRange]]
    hours_notes: str = ""
    # Full-day closures (statutory holidays, staff training days). Nothing is offered on these dates.
    closed_dates: list[date] = []
    services: list[Service] = Field(min_length=1)
    faqs: list[FAQ] = []
    greeting: str
    recording: Recording = Recording()
    escalation: Escalation
    voice: Voice = Voice()
    booking: Booking = Booking()
    notifications: Notifications
    limits: Limits = Limits()

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown IANA timezone {v!r}") from e
        return v

    @field_validator("hours")
    @classmethod
    def _no_overlap(cls, v: dict[Weekday, list[TimeRange]]) -> dict[Weekday, list[TimeRange]]:
        for day, ranges in v.items():
            ordered = sorted(ranges, key=lambda r: r.open)
            for a, b in zip(ordered, ordered[1:]):
                if b.open < a.close:
                    raise ValueError(f"{day.value}: overlapping ranges {a.open}-{a.close} and {b.open}-{b.close}")
        return v

    @field_validator("services")
    @classmethod
    def _unique_services(cls, v: list[Service]) -> list[Service]:
        ids = [s.id for s in v]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate service ids: {sorted(dupes)}")
        return v

    @model_validator(mode="after")
    def _greeting_disclosures(self) -> ClinicConfig:
        if not AI_DISCLOSURE.search(self.greeting):
            raise ValueError("greeting must disclose that the caller is speaking with an AI")
        if self.recording.enabled and not RECORDING_NOTICE.search(self.greeting):
            raise ValueError("recording is enabled, so the greeting must include a recording notice")
        return self

    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def hours_on(self, day: date) -> list[TimeRange]:
        if day in self.closed_dates:
            return []
        return list(self.hours.get(list(Weekday)[day.weekday()], []))

    def service(self, service_id: str) -> Service | None:
        return next((s for s in self.services if s.id == service_id), None)


def clinic_dir(clinic: str, root: Path = CLINICS_ROOT) -> Path:
    return root / clinic


def load_clinic_config(path: Path) -> ClinicConfig:
    """Load `clinic.yaml` from a clinic folder (or a direct file path)."""
    file = path / "clinic.yaml" if path.is_dir() else path
    if not file.exists():
        raise ConfigError(f"clinic config not found: {file}")
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{file}: invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{file}: expected a mapping at the top level")
    try:
        return ClinicConfig.model_validate(raw)
    except ValidationError as e:
        lines = [f"{file}: {e.error_count()} problem(s)"]
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "(root)"
            lines.append(f"  - {loc}: {err['msg']}")
        raise ConfigError("\n".join(lines)) from None
