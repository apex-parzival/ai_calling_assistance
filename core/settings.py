"""Secrets and environment settings, loaded from `clinics/<CLINIC>/.env` plus the process env.

Process environment variables win over the .env file (so docker-compose can
override e.g. DATABASE_URL). Keys are grouped by which process needs them;
each process calls `require()` for its groups at startup so a missing or
malformed key fails before the first call, not during one.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.config import CLINICS_ROOT

Group = Literal["backend", "agent", "telephony", "notify"]
ALL_GROUPS: tuple[Group, ...] = ("backend", "agent", "telephony", "notify")

# Values people leave in by accident when copying .env.example.
PLACEHOLDER = re.compile(r"^(changeme|change-me|replace|your[-_ ]|xxx|<|todo)", re.IGNORECASE)


class SettingsError(Exception):
    """Raised when required settings are missing or malformed."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_file_encoding="utf-8")

    clinic: str = "demo_clinic"
    clinics_root: Path = CLINICS_ROOT
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"

    # backend
    database_url: str | None = None
    session_secret: SecretStr | None = None
    agent_api_token: SecretStr | None = None
    backend_url: str = "http://localhost:8000"
    dashboard_origin: str = "http://localhost:3000"  # CORS + WebSocket origin for the staff dashboard
    session_max_age_hours: int = 12

    # agent: media + voice + LLM
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: SecretStr | None = None
    deepgram_api_key: SecretStr | None = None
    cartesia_api_key: SecretStr | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    # Qwen3 "thinking" adds seconds of latency on a call; on by default to turn it off.
    llm_disable_thinking: bool = True

    # telephony (Phase 2) and SMS
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_from_number: str | None = None

    # staff notifications
    notify_channel: Literal["log", "sendgrid", "smtp", "twilio_sms"] = "log"
    notify_from_email: str | None = None
    sendgrid_api_key: SecretStr | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None

    @property
    def clinic_dir(self) -> Path:
        return self.clinics_root / self.clinic


def load_settings(**overrides) -> Settings:
    """Read CLINIC from the environment, then load that clinic's .env."""
    clinic = overrides.get("clinic") or os.environ.get("CLINIC", "demo_clinic")
    root = Path(overrides.get("clinics_root") or os.environ.get("CLINICS_ROOT", CLINICS_ROOT))
    env_file = root / clinic / ".env"
    return Settings(_env_file=env_file if env_file.exists() else None, **{"clinic": clinic, **overrides})


# --- validation -------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    field: str
    group: Group
    check: Callable[[str], str | None] | None = None  # returns an error message or None
    when: Callable[[Settings], bool] = lambda s: True


def _regex(pattern: str, hint: str) -> Callable[[str], str | None]:
    rx = re.compile(pattern)
    return lambda v: None if rx.match(v) else f"expected {hint}"


def _min_len(n: int) -> Callable[[str], str | None]:
    return lambda v: None if len(v) >= n else f"must be at least {n} characters"


def _notify(channel: str) -> Callable[[Settings], bool]:
    return lambda s: s.notify_channel == channel


RULES: tuple[Rule, ...] = (
    Rule("database_url", "backend",
         _regex(r"^(postgresql(\+asyncpg)?|sqlite\+aiosqlite)://", "postgresql+asyncpg://user:pass@host:5432/db")),
    Rule("session_secret", "backend", _min_len(32)),
    Rule("agent_api_token", "backend", _min_len(24)),
    Rule("livekit_url", "agent", _regex(r"^wss?://", "wss://<project>.livekit.cloud")),
    Rule("livekit_api_key", "agent", _regex(r"^API\w+$", "a key starting with 'API'")),
    Rule("livekit_api_secret", "agent", _min_len(20)),
    Rule("deepgram_api_key", "agent", _min_len(20)),
    Rule("cartesia_api_key", "agent", _min_len(20)),
    Rule("llm_api_key", "agent", _min_len(20)),
    Rule("llm_base_url", "agent", _regex(r"^https?://", "an http(s) URL ending in /v1 or /openai/v1")),
    Rule("llm_model", "agent"),
    Rule("agent_api_token", "agent", _min_len(24)),
    Rule("backend_url", "agent", _regex(r"^https?://", "an http(s) URL")),
    Rule("twilio_account_sid", "telephony", _regex(r"^AC[0-9a-fA-F]{32}$", "'AC' followed by 32 hex characters")),
    Rule("twilio_auth_token", "telephony", _regex(r"^[0-9a-fA-F]{32}$", "32 hex characters")),
    Rule("sendgrid_api_key", "notify", _regex(r"^SG\.", "a key starting with 'SG.'"), when=_notify("sendgrid")),
    Rule("notify_from_email", "notify", _regex(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", "an email address"),
         when=lambda s: s.notify_channel in ("sendgrid", "smtp")),
    Rule("smtp_host", "notify", when=_notify("smtp")),
    Rule("smtp_username", "notify", when=_notify("smtp")),
    Rule("smtp_password", "notify", when=_notify("smtp")),
    Rule("twilio_account_sid", "notify", when=_notify("twilio_sms")),
    Rule("twilio_auth_token", "notify", when=_notify("twilio_sms")),
    Rule("twilio_from_number", "notify", _regex(r"^\+[1-9]\d{7,14}$", "an E.164 number"), when=_notify("twilio_sms")),
)


def _raw(settings: Settings, field: str) -> str | None:
    value = getattr(settings, field)
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def problems(settings: Settings, groups: Iterable[Group]) -> list[str]:
    """Return one human-readable line per missing or malformed setting."""
    wanted = set(groups)
    out: list[str] = []
    seen: set[str] = set()
    for rule in RULES:
        if rule.group not in wanted or rule.field in seen or not rule.when(settings):
            continue
        seen.add(rule.field)
        env = rule.field.upper()
        value = _raw(settings, rule.field)
        if value is None:
            out.append(f"{env}: missing ({rule.group})")
        elif PLACEHOLDER.match(value):
            out.append(f"{env}: still a placeholder value ({rule.group})")
        elif rule.check and (msg := rule.check(value)):
            out.append(f"{env}: {msg} ({rule.group})")
    if "notify" in wanted and settings.environment == "production" and settings.notify_channel == "log":
        out.append("NOTIFY_CHANNEL: 'log' is not allowed in production (notify)")
    if "backend" in wanted and settings.environment == "production" and (settings.database_url or "").startswith("sqlite"):
        out.append("DATABASE_URL: SQLite is for tests and Docker-free local dev only; production needs PostgreSQL (backend)")
    return out


def require(settings: Settings, groups: Iterable[Group]) -> None:
    found = problems(settings, groups)
    if found:
        where = settings.clinic_dir / ".env"
        raise SettingsError(f"invalid settings (from {where} + environment):\n  - " + "\n  - ".join(found))
