"""System prompt: built only from clinic.yaml, with explicit dates so a small model needn't do date maths."""

from __future__ import annotations

import datetime as dt

from core.config import load_clinic_config
from core.prompts import build_system_prompt


def _prompt(clinics_root, when: dt.datetime) -> str:
    clinic = load_clinic_config(clinics_root / "demo_clinic")
    return build_system_prompt(clinic, when.replace(tzinfo=clinic.tz()))


def test_calendar_marks_this_week_and_next_week(clinics_root):
    # Monday 9 November 2026. "Next week" starts Monday the 16th, not tomorrow.
    p = _prompt(clinics_root, dt.datetime(2026, 11, 9, 10, 0))
    assert "Monday November 9 = 2026-11-09 (today, this week)" in p
    assert "Tuesday November 10 = 2026-11-10 (tomorrow, this week)" in p
    assert "Monday November 16 = 2026-11-16 (next week)" in p
    assert "Sunday November 22 = 2026-11-22 (next week), CLOSED" in p
    assert '"Next week" means the dates marked next week' in p


def test_calendar_days_past_next_week_have_no_label(clinics_root):
    # Thursday: the last days of the 14-day window fall in the week after next.
    p = _prompt(clinics_root, dt.datetime(2026, 11, 12, 10, 0))
    assert "Monday November 23 = 2026-11-23, " in p
    assert "()" not in p


def test_disclosure_and_no_think(clinics_root):
    p = _prompt(clinics_root, dt.datetime(2026, 11, 9, 10, 0))
    assert "Maplegate Family Clinic" in p
    assert p.rstrip().endswith("/no_think")


def test_intake_prompt_is_built_from_config(clinics_root):
    from core.prompts import build_intake_prompt
    clinic = load_clinic_config(clinics_root / "demo_clinic")
    p = build_intake_prompt(clinic, dt.datetime(2026, 11, 9, 10, 0, tzinfo=clinic.tz()))
    assert "Maplegate Family Clinic" in p and "save_callback_request" in p
    assert all(s.name in p for s in clinic.services)
    assert "The best phone number to reach them." in p
    assert p.rstrip().endswith("/no_think")


def test_intake_prompt_offers_caller_id(clinics_root):
    from core.prompts import build_intake_prompt
    clinic = load_clinic_config(clinics_root / "demo_clinic")
    p = build_intake_prompt(clinic, dt.datetime(2026, 11, 9, 10, 0, tzinfo=clinic.tz()), caller_phone="+14035550111")
    assert "phoning from 4 0 3, 5 5 5, 0 1 1 1" in p
