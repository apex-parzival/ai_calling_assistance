"""STT adapters, chosen by clinic.yaml `voice.stt_provider`."""

from __future__ import annotations

from core.config import ClinicConfig
from core.settings import Settings


def make_stt(clinic: ClinicConfig, settings: Settings):
    provider = clinic.voice.stt_provider
    if provider == "deepgram":
        from livekit.plugins import deepgram

        return deepgram.STT(
            model="nova-3",
            language=clinic.voice.language.split("-")[0],  # "en" covers Canadian English on nova-3
            api_key=settings.deepgram_api_key.get_secret_value(),
            smart_format=True,   # "four oh three" -> 403, better for phone numbers
            filler_words=True,
            # Bias recognition toward words this clinic's callers will say.
            keyterm=[clinic.name, *(s.name for s in clinic.services)][:50],
        )
    if provider == "faster_whisper":
        # Self-hosted fallback if US speech vendors are rejected (CLAUDE.md §10). Not built yet.
        raise NotImplementedError("faster-whisper STT adapter is not implemented yet")
    raise ValueError(f"unknown stt_provider {provider!r}")
