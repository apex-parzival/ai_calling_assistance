from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.config import ConfigError, load_clinic_config

from .conftest import DEMO_YAML


def write(tmp_path: Path, raw: dict) -> Path:
    p = tmp_path / "clinic.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return p


def test_demo_clinic_loads():
    cfg = load_clinic_config(DEMO_YAML.parent)
    assert cfg.clinic_id == "demo_clinic"
    assert cfg.address.city == "Calgary"
    assert cfg.tz().key == "America/Edmonton"
    assert cfg.service("standard_visit").duration_minutes == 15
    assert not cfg.hours.get("sunday")


def test_missing_file_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_clinic_config(tmp_path)


def test_greeting_must_disclose_ai(tmp_path, demo_raw):
    demo_raw["greeting"] = "Thanks for calling. This call is recorded. How can I help?"
    with pytest.raises(ConfigError, match="disclose"):
        load_clinic_config(write(tmp_path, demo_raw))


def test_greeting_must_mention_recording_when_enabled(tmp_path, demo_raw):
    demo_raw["greeting"] = "Hi, I'm the clinic's AI assistant. How can I help?"
    with pytest.raises(ConfigError, match="recording notice"):
        load_clinic_config(write(tmp_path, demo_raw))
    demo_raw["recording"] = {"enabled": False}
    assert load_clinic_config(write(tmp_path, demo_raw))


def test_ai_word_match_is_whole_word(tmp_path, demo_raw):
    # "maintain" contains "ai" but is not a disclosure.
    demo_raw["greeting"] = "We maintain recorded lines. How can I help?"
    with pytest.raises(ConfigError, match="disclose"):
        load_clinic_config(write(tmp_path, demo_raw))


def test_bad_hours_rejected(tmp_path, demo_raw):
    demo_raw["hours"]["monday"] = [{"open": "17:00", "close": "08:00"}]
    with pytest.raises(ConfigError, match="hours.monday"):
        load_clinic_config(write(tmp_path, demo_raw))


def test_overlapping_hours_rejected(tmp_path, demo_raw):
    demo_raw["hours"]["monday"] = [{"open": "08:00", "close": "12:00"}, {"open": "11:00", "close": "17:00"}]
    with pytest.raises(ConfigError, match="overlapping"):
        load_clinic_config(write(tmp_path, demo_raw))


def test_transfer_number_must_be_e164(tmp_path, demo_raw):
    demo_raw["escalation"]["transfer_number"] = "403-555-0199"
    with pytest.raises(ConfigError, match="E.164"):
        load_clinic_config(write(tmp_path, demo_raw))


def test_unknown_timezone_rejected(tmp_path, demo_raw):
    demo_raw["timezone"] = "Mars/Olympus"
    with pytest.raises(ConfigError, match="timezone"):
        load_clinic_config(write(tmp_path, demo_raw))


def test_unknown_keys_rejected(tmp_path, demo_raw):
    demo_raw["adress"] = {}  # typo should not be silently ignored
    with pytest.raises(ConfigError, match="adress"):
        load_clinic_config(write(tmp_path, demo_raw))


def test_staff_contact_required(tmp_path, demo_raw):
    demo_raw["notifications"] = {"staff_emails": [], "staff_sms": []}
    with pytest.raises(ConfigError, match="staff"):
        load_clinic_config(write(tmp_path, demo_raw))


def test_end_of_turn_silence_default_and_bounds(tmp_path, demo_raw):
    demo_raw["voice"].pop("end_of_turn_silence", None)
    assert load_clinic_config(write(tmp_path, demo_raw)).voice.end_of_turn_silence == 0.55  # LiveKit default
    for bad in (0.1, 3):  # too short cuts callers off mid-sentence; too long is dead air
        demo_raw["voice"]["end_of_turn_silence"] = bad
        with pytest.raises(ConfigError, match="end_of_turn_silence"):
            load_clinic_config(write(tmp_path, demo_raw))


def test_duplicate_service_ids_rejected(tmp_path, demo_raw):
    demo_raw["services"].append(dict(demo_raw["services"][0]))
    with pytest.raises(ConfigError, match="duplicate"):
        load_clinic_config(write(tmp_path, demo_raw))


def test_callback_message_has_a_default(demo_raw, tmp_path):
    raw = {k: v for k, v in demo_raw.items() if k != "callback_message"}
    (tmp_path / "clinic.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert "call you back" in load_clinic_config(tmp_path).callback_message
