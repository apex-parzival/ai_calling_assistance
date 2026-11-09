from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from core.config import CLINICS_ROOT

DEMO_YAML = CLINICS_ROOT / "demo_clinic" / "clinic.yaml"

# Fake but well-formed values. Never real keys.
VALID_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
    "SESSION_SECRET": "s" * 40,
    "AGENT_API_TOKEN": "t" * 30,
    "BACKEND_URL": "http://localhost:8000",
    "LIVEKIT_URL": "wss://demo.livekit.cloud",
    "LIVEKIT_API_KEY": "APIabc123def456",
    "LIVEKIT_API_SECRET": "k9" * 20,
    "DEEPGRAM_API_KEY": "d" * 40,
    "CARTESIA_API_KEY": "c" * 40,
    "LLM_API_KEY": "l" * 40,
    "LLM_BASE_URL": "https://openrouter.ai/api/v1",
    "LLM_MODEL": "qwen/qwen3-14b",
    "TWILIO_ACCOUNT_SID": "AC" + "0" * 32,
    "TWILIO_AUTH_TOKEN": "f" * 32,
    "NOTIFY_CHANNEL": "log",
}


@pytest.fixture
def demo_raw() -> dict:
    return copy.deepcopy(yaml.safe_load(DEMO_YAML.read_text(encoding="utf-8")))


@pytest.fixture
def clinics_root(tmp_path: Path, demo_raw: dict) -> Path:
    """A temp clinics/ folder with a copy of the demo clinic and no .env file."""
    d = tmp_path / "demo_clinic"
    d.mkdir()
    (d / "clinic.yaml").write_text(yaml.safe_dump(demo_raw), encoding="utf-8")
    return tmp_path


@pytest.fixture
def clean_env(monkeypatch):
    """Remove any real settings from the process env so tests are hermetic."""
    for key in [*VALID_ENV, "CLINIC", "CLINICS_ROOT", "ENVIRONMENT", "SENDGRID_API_KEY", "NOTIFY_FROM_EMAIL"]:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch
