from __future__ import annotations

import pytest

from core.settings import ALL_GROUPS, SettingsError, load_settings, problems, require

from .conftest import VALID_ENV


def settings_with(clean_env, clinics_root, **env):
    for k, v in {**VALID_ENV, **env}.items():
        if v is None:
            clean_env.delenv(k, raising=False)
        else:
            clean_env.setenv(k, v)
    return load_settings(clinics_root=clinics_root)


def test_valid_env_passes_every_group(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root)
    assert problems(s, ALL_GROUPS) == []


def test_missing_key_reported(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, DEEPGRAM_API_KEY=None)
    assert problems(s, ["agent"]) == ["DEEPGRAM_API_KEY: missing (agent)"]
    assert problems(s, ["backend"]) == []


def test_empty_value_counts_as_missing(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, SESSION_SECRET="   ")
    assert "SESSION_SECRET: missing (backend)" in problems(s, ["backend"])


def test_placeholder_rejected(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, LLM_API_KEY="your-groq-key-here-xxxxxxxx")
    assert any("placeholder" in p for p in problems(s, ["agent"]))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("LIVEKIT_URL", "https://demo.livekit.cloud"),
        ("LIVEKIT_API_KEY", "abc123def456"),
        ("TWILIO_ACCOUNT_SID", "SK" + "0" * 32),
        ("DATABASE_URL", "mysql://u:p@h/db"),
        ("SESSION_SECRET", "short"),
    ],
)
def test_malformed_values_rejected(clean_env, clinics_root, key, value):
    s = settings_with(clean_env, clinics_root, **{key: value})
    assert any(p.startswith(key) for p in problems(s, ALL_GROUPS))


def test_sendgrid_channel_requires_its_keys(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, NOTIFY_CHANNEL="sendgrid")
    found = problems(s, ["notify"])
    assert "SENDGRID_API_KEY: missing (notify)" in found
    assert "NOTIFY_FROM_EMAIL: missing (notify)" in found


def test_log_channel_refused_in_production(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, ENVIRONMENT="production")
    assert any("NOTIFY_CHANNEL" in p for p in problems(s, ["notify"]))


def test_require_lists_all_problems(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, SESSION_SECRET=None, AGENT_API_TOKEN=None)
    with pytest.raises(SettingsError) as e:
        require(s, ["backend"])
    assert "SESSION_SECRET" in str(e.value) and "AGENT_API_TOKEN" in str(e.value)


def test_clinic_env_file_is_read_and_process_env_wins(clean_env, clinics_root):
    (clinics_root / "demo_clinic" / ".env").write_text(
        "SESSION_SECRET=from-file-" + "a" * 30 + "\nLLM_MODEL=from-file  # inline comment\n", encoding="utf-8"
    )
    clean_env.setenv("LLM_MODEL", "from-env")
    s = load_settings(clinics_root=clinics_root)
    assert s.session_secret.get_secret_value().startswith("from-file-")
    assert s.llm_model == "from-env"
