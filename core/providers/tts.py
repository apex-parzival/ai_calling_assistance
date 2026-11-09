"""TTS adapters, chosen by clinic.yaml `voice.tts_provider`.

cartesia  Cartesia cloud API: the production default.
kokoro    Self-hosted Kokoro-FastAPI (OpenAI-compatible /v1/audio/speech): local development
          and testing. deploy/docker-compose.yml runs it as the `kokoro` service.

Switching provider is a clinic.yaml change; each branch needs only its own settings.
"""

from __future__ import annotations

from core.config import ClinicConfig
from core.settings import Settings

KOKORO_SAMPLE_RATE = 24000  # Kokoro's native rate; raw PCM at this rate needs no decoding


def make_tts(clinic: ClinicConfig, settings: Settings):
    provider = clinic.voice.tts_provider
    if provider == "cartesia":
        from livekit.plugins import cartesia

        return cartesia.TTS(
            api_key=settings.cartesia_api_key.get_secret_value(),
            voice=clinic.voice.voice_id,
            language=clinic.voice.language.split("-")[0],
        )
    if provider == "kokoro":
        from livekit.plugins import openai

        # livekit-plugins-openai 1.8.11: TTS(model, voice, speed, sample_rate, instructions,
        # base_url, api_key, client, response_format). It reads the server's Content-Type, so
        # Kokoro's raw "audio/pcm" is played as-is.
        return openai.TTS(
            model="kokoro",
            voice=clinic.voice.voice_id or settings.kokoro_voice,
            base_url=settings.kokoro_base_url,
            api_key="kokoro",  # ignored by Kokoro-FastAPI; the OpenAI client needs a value
            response_format="pcm",
            sample_rate=KOKORO_SAMPLE_RATE,
        )
    if provider == "elevenlabs":
        raise NotImplementedError(f"{provider} TTS adapter is not implemented yet")
    raise ValueError(f"unknown tts_provider {provider!r}")
