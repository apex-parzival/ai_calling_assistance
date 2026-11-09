"""Phase 1 exit criterion: a second clinic folder can be added and run with no code change."""

from __future__ import annotations

import datetime as dt

import yaml
from fastapi.testclient import TestClient

from backend.main import create_app
from core.config import load_clinic_config
from core.prompts import build_system_prompt
from core.settings import load_settings

from .conftest import VALID_ENV


def test_second_clinic_runs_without_code_changes(clean_env, clinics_root, demo_raw):
    other = dict(demo_raw)
    other.update(
        clinic_id="riverside_walk_in",
        name="Riverside Walk-In Clinic",
        timezone="America/Edmonton",
        main_phone="+15875550100",
        address={"street": "12 River Road", "city": "Red Deer", "province": "AB", "postal_code": "T4N 0A1"},
        greeting="Hello, you've reached Riverside Walk-In Clinic. I'm an automated assistant and this call is "
                 "recorded. If this is an emergency, hang up and call 9-1-1.",
        hours={"monday": [{"open": "10:00", "close": "14:00"}]},
        closed_dates=[],
        services=[{"id": "walk_in_consult", "name": "Walk-in consult", "duration_minutes": 10}],
        faqs=[{"topic": "parking", "question": "Parking?", "answer": "Street parking only."}],
        escalation={**demo_raw["escalation"], "transfer_number": "+15875550101"},
    )
    folder = clinics_root / "riverside_walk_in"
    folder.mkdir()
    (folder / "clinic.yaml").write_text(yaml.safe_dump(other), encoding="utf-8")

    for k, v in {**VALID_ENV, "DATABASE_URL": "sqlite+aiosqlite:///:memory:", "CLINIC": "riverside_walk_in"}.items():
        clean_env.setenv(k, v)
    settings = load_settings(clinics_root=clinics_root)
    assert settings.clinic == "riverside_walk_in"

    clinic = load_clinic_config(settings.clinic_dir)
    prompt = build_system_prompt(clinic, dt.datetime(2027, 3, 1, 17, tzinfo=dt.timezone.utc))
    assert "Riverside Walk-In Clinic" in prompt and "Walk-in consult" in prompt
    assert "Maplegate" not in prompt  # nothing leaks from the demo clinic

    with TestClient(create_app(settings)) as client:
        assert client.app.state.clinic.clinic_id == "riverside_walk_in"
        headers = {"Authorization": f"Bearer {VALID_ENV['AGENT_API_TOKEN']}"}
        r = client.post("/agent/transfer", json={}, headers=headers)
        assert r.json()["number"] == "+15875550101"
