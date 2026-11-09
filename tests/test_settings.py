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


def test_env_example_has_no_inline_comments():
    # docker compose reads `KEY=   # note` as the value "# note"; python-dotenv strips it.
    # The two must agree (the agent and the backend share AGENT_API_TOKEN), so no inline comments.
    import re

    from core.config import CLINICS_ROOT
    for example in CLINICS_ROOT.glob("*/.env.example"):
        bad = [line for line in example.read_text(encoding="utf-8").splitlines()
               if re.match(r"^[A-Z][A-Z0-9_]*=.*\s#", line)]
        assert bad == [], f"{example}: put comments on their own line: {bad}"


def test_llm_extra_body_parsed(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, LLM_EXTRA_BODY='{"provider": {"sort": "latency"}}')
    assert problems(s, ["agent"]) == []
    assert s.llm_extra == {"provider": {"sort": "latency"}}
    assert settings_with(clean_env, clinics_root, LLM_EXTRA_BODY=None).llm_extra == {}


@pytest.mark.parametrize("value", ['{"provider": ', '["not", "an", "object"]'])
def test_llm_extra_body_must_be_a_json_object(clean_env, clinics_root, value):
    s = settings_with(clean_env, clinics_root, LLM_EXTRA_BODY=value)
    assert any(p.startswith("LLM_EXTRA_BODY") for p in problems(s, ["agent"]))


def test_local_ollama_accepts_a_short_key(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, LLM_BASE_URL="http://localhost:11434/v1",
                      LLM_API_KEY="ollama", LLM_LOCALHOST_ALIAS=None)
    assert problems(s, ["agent"]) == []
    assert s.llm_url == "http://localhost:11434/v1"


def test_local_llm_still_needs_some_key(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, LLM_BASE_URL="http://127.0.0.1:11434/v1", LLM_API_KEY=None)
    assert problems(s, ["agent"]) == ["LLM_API_KEY: missing (agent)"]


def test_hosted_llm_still_needs_a_real_key(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, LLM_API_KEY="ollama")
    assert any(p.startswith("LLM_API_KEY:") for p in problems(s, ["agent"]))


def test_localhost_llm_rewritten_inside_docker(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, LLM_BASE_URL="http://localhost:11434/v1",
                      LLM_API_KEY="ollama", LLM_LOCALHOST_ALIAS="host.docker.internal")
    assert s.llm_url == "http://host.docker.internal:11434/v1"


def test_hosted_llm_url_not_rewritten(clean_env, clinics_root):
    s = settings_with(clean_env, clinics_root, LLM_LOCALHOST_ALIAS="host.docker.internal")
    assert s.llm_url == "https://openrouter.ai/api/v1"


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
