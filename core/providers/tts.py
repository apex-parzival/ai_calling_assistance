"""TTS adapters, chosen by clinic.yaml `voice.tts_provider`."""

from __future__ import annotations

from core.config import ClinicConfig
from core.settings import Settings


def make_tts(clinic: ClinicConfig, settings: Settings):
    provider = clinic.voice.tts_provider
    if provider == "cartesia":
        from livekit.plugins import cartesia

        return cartesia.TTS(
            api_key=settings.cartesia_api_key.get_secret_value(),
            voice=clinic.voice.voice_id,
            language=clinic.voice.language.split("-")[0],
        )
    if provider in ("elevenlabs", "kokoro"):
        raise NotImplementedError(f"{provider} TTS adapter is not implemented yet")
    raise ValueError(f"unknown tts_provider {provider!r}")
