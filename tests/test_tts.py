"""TTS adapter selection: each provider works with only its own settings."""

from __future__ import annotations

import pytest
import yaml

from core.config import load_clinic_config
from core.providers.tts import KOKORO_SAMPLE_RATE, make_tts
from core.settings import SettingsError, load_settings, problems

from .conftest import VALID_ENV

CARTESIA_VOICE = "1242fb95-7ddd-44ac-8a05-9e8a22a6137d"


def setup(clean_env, clinics_root, demo_raw, provider, voice_id, **env):
    demo_raw["voice"].update(tts_provider=provider, voice_id=voice_id)
    (clinics_root / "demo_clinic" / "clinic.yaml").write_text(yaml.safe_dump(demo_raw), encoding="utf-8")
    for k, v in {**VALID_ENV, **env}.items():
        if v is None:
            clean_env.delenv(k, raising=False)
        else:
            clean_env.setenv(k, v)
    clean_env.setenv("CLINICS_ROOT", str(clinics_root))
    settings = load_settings(clinics_root=clinics_root)
    return settings, load_clinic_config(settings.clinic_dir)


def test_cartesia_works_without_any_kokoro_settings(clean_env, clinics_root, demo_raw):
    from livekit.plugins import cartesia

    settings, clinic = setup(clean_env, clinics_root, demo_raw, "cartesia", CARTESIA_VOICE)
    assert problems(settings, ["agent"], tts_provider="cartesia") == []
    tts = make_tts(clinic, settings)
    assert isinstance(tts, cartesia.TTS)


def test_kokoro_works_without_a_cartesia_key(clean_env, clinics_root, demo_raw):
    from livekit.plugins import openai

    settings, clinic = setup(clean_env, clinics_root, demo_raw, "kokoro", "af_heart",
                             CARTESIA_API_KEY=None, KOKORO_BASE_URL="http://kokoro:8880/v1")
    assert settings.cartesia_api_key is None
    assert problems(settings, ["agent"], tts_provider="kokoro") == []
    tts = make_tts(clinic, settings)
    assert isinstance(tts, openai.TTS)
    assert tts.sample_rate == KOKORO_SAMPLE_RATE
    assert str(tts._client.base_url).rstrip("/") == "http://kokoro:8880/v1"
    assert tts._opts.model == "kokoro" and tts._opts.voice == "af_heart" and tts._opts.response_format == "pcm"


def test_kokoro_falls_back_to_kokoro_voice(clean_env, clinics_root, demo_raw):
    settings, clinic = setup(clean_env, clinics_root, demo_raw, "kokoro", None,
                             CARTESIA_API_KEY=None, KOKORO_VOICE="am_michael")
    assert make_tts(clinic, settings)._opts.voice == "am_michael"


def test_kokoro_defaults(clean_env, clinics_root, demo_raw):
    settings, _ = setup(clean_env, clinics_root, demo_raw, "kokoro", "af_heart", CARTESIA_API_KEY=None)
    assert settings.kokoro_base_url == "http://localhost:8880/v1"
    assert settings.kokoro_voice == "af_heart"


def test_cartesia_selected_without_key_names_the_variable(clean_env, clinics_root, demo_raw):
    settings, _ = setup(clean_env, clinics_root, demo_raw, "cartesia", CARTESIA_VOICE, CARTESIA_API_KEY=None)
    found = problems(settings, ["agent"], tts_provider="cartesia")
    assert len(found) == 1 and found[0].startswith("CARTESIA_API_KEY: missing")
    assert "voice.tts_provider is cartesia" in found[0]


def test_kokoro_bad_url_names_the_variable(clean_env, clinics_root, demo_raw):
    settings, _ = setup(clean_env, clinics_root, demo_raw, "kokoro", "af_heart",
                        CARTESIA_API_KEY=None, KOKORO_BASE_URL="localhost:8880")
    found = problems(settings, ["agent"], tts_provider="kokoro")
    assert len(found) == 1 and found[0].startswith("KOKORO_BASE_URL:")


def test_elevenlabs_still_not_implemented(clean_env, clinics_root, demo_raw):
    settings, clinic = setup(clean_env, clinics_root, demo_raw, "elevenlabs", "x")
    with pytest.raises(NotImplementedError):
        make_tts(clinic, settings)
    assert any("not implemented" in p for p in problems(settings, ["agent"], tts_provider="elevenlabs"))


@pytest.mark.parametrize(("provider", "voice", "env", "ok"), [
    ("kokoro", "af_heart", {"CARTESIA_API_KEY": None}, True),
    ("cartesia", CARTESIA_VOICE, {}, True),
    ("cartesia", CARTESIA_VOICE, {"CARTESIA_API_KEY": None}, False),
])
def test_agent_startup_check_follows_the_selected_provider(clean_env, clinics_root, demo_raw,
                                                           provider, voice, env, ok):
    from core import agent

    setup(clean_env, clinics_root, demo_raw, provider, voice, **env)
    agent.config.cache_clear()
    try:
        if ok:
            settings, clinic = agent.config()
            assert clinic.voice.tts_provider == provider
        else:
            with pytest.raises(SettingsError, match="CARTESIA_API_KEY: missing"):
                agent.config()
    finally:
        agent.config.cache_clear()


def test_check_env_passes_for_kokoro_without_cartesia_key(clean_env, clinics_root, demo_raw, capsys):
    from core import check_env

    setup(clean_env, clinics_root, demo_raw, "kokoro", "af_heart", CARTESIA_API_KEY=None)
    assert check_env.main(["--groups", "agent"]) == 0
    out = capsys.readouterr().out
    assert "TTS provider (clinic.yaml voice.tts_provider): kokoro" in out and "CARTESIA" not in out


def test_check_env_fails_for_cartesia_without_key(clean_env, clinics_root, demo_raw, capsys):
    from core import check_env

    setup(clean_env, clinics_root, demo_raw, "cartesia", CARTESIA_VOICE, CARTESIA_API_KEY=None)
    assert check_env.main(["--groups", "agent"]) == 1
    assert "CARTESIA_API_KEY: missing" in capsys.readouterr().out
