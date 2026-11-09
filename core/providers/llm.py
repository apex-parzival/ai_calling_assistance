"""LLM adapter: any OpenAI-compatible endpoint (Groq, OpenRouter, or self-hosted vLLM later).

Swapping providers is a LLM_BASE_URL / LLM_MODEL change in the clinic's .env.
"""

from __future__ import annotations

from core.settings import Settings


def make_llm(settings: Settings):
    from livekit.plugins import openai

    return openai.LLM(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        temperature=0.3,           # receptionist, not a poet
        parallel_tool_calls=False,  # small models get confused running tools in parallel
    )
