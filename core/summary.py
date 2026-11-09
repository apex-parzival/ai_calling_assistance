"""Post-call summary for the front desk. Runs after hang-up, never during the call."""

from __future__ import annotations

import json
import logging
import re

import httpx

from core.settings import Settings

log = logging.getLogger("agent.summary")

PROMPT = """You summarise phone calls to a medical clinic for the front desk.
Write a factual summary of 1 to 3 short sentences: who called, what they wanted, and what was done or still needs doing (callback, confirm booking, etc).
Also give a reason of at most 8 words.
Do not add medical opinions. Do not invent details not in the transcript.
Reply with only JSON: {"summary": "...", "reason": "..."}"""


async def summarize(settings: Settings, transcript: list[dict]) -> tuple[str, str | None] | None:
    lines = [f"{'Caller' if t['role'] == 'user' else 'Receptionist'}: {t['text']}"
             for t in transcript if t["role"] in ("user", "assistant") and t.get("text")]
    if not lines:
        return None
    body = {
        **settings.llm_extra,
        "model": settings.llm_model,
        "temperature": 0,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": PROMPT + ("\n/no_think" if settings.llm_disable_thinking else "")},
            {"role": "user", "content": "\n".join(lines)[-12000:]},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{settings.llm_base_url.rstrip('/')}/chat/completions", json=body,
                             headers={"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}"})
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"] or ""
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        log.warning("summary failed: %s", e)
        return None
    return parse_summary(content)


def parse_summary(content: str) -> tuple[str, str | None] | None:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if data.get("summary"):
                return str(data["summary"])[:4000], (str(data["reason"])[:255] if data.get("reason") else None)
        except json.JSONDecodeError:
            pass
    # Small models sometimes ignore the JSON instruction; keep the text rather than lose it.
    return (content[:4000], None) if content else None
