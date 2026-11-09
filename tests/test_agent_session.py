"""The real Receptionist inside a real AgentSession (text mode), driven by a scripted LLM.

Proves the guardrails hold at the session level: emergency language never reaches
the LLM, and advice the LLM does produce never reaches the caller.
"""

from __future__ import annotations

import json
import re
import uuid

import httpx
import pytest
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, AgentSession, llm

from core.agent import Receptionist
from core.backend_client import BackendClient
from core.config import load_clinic_config
from core.guardrails import CRISIS_MESSAGE, EMERGENCY_MESSAGE, SAFE_REDIRECT
from core.settings import load_settings
from core.tools import CallState

from .conftest import VALID_ENV


@pytest.fixture
def anyio_backend():
    return "asyncio"


class ScriptedLLM(llm.LLM):
    """Returns canned replies in order and records every request it receives."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__()
        self.replies = list(replies)
        self.requests: list[llm.ChatContext] = []

    def chat(self, *, chat_ctx, tools=None, conn_options=DEFAULT_API_CONNECT_OPTIONS, **_kw):
        self.requests.append(chat_ctx.copy())
        text = self.replies.pop(0) if self.replies else "Okay."
        return _ScriptedStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options, text=text)


class _ScriptedStream(llm.LLMStream):
    def __init__(self, owner, *, text: str, **kw) -> None:
        super().__init__(owner, **kw)
        self._text = text

    async def _run(self) -> None:
        for piece in re.findall(r"\S+\s*", self._text):  # stream word by word, like a real model
            self._event_ch.send_nowait(llm.ChatChunk(id="s", delta=llm.ChoiceDelta(role="assistant", content=piece)))


@pytest.fixture
def backend_calls():
    return []


@pytest.fixture
def make_session(clean_env, clinics_root, tmp_path, backend_calls):
    for k, v in VALID_ENV.items():
        clean_env.setenv(k, v)
    settings = load_settings(clinics_root=clinics_root)
    clinic = load_clinic_config(settings.clinic_dir)

    def handler(request: httpx.Request) -> httpx.Response:
        backend_calls.append((request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(200, json={"id": str(uuid.uuid4())})

    async def make(replies: list[str]):
        scripted = ScriptedLLM(replies)
        backend = BackendClient(settings, spool_dir=tmp_path / "spool", transport=httpx.MockTransport(handler))
        state = CallState()
        agent = Receptionist(clinic, settings, backend, state, job_ctx=None)
        session = AgentSession(llm=scripted)
        await session.start(agent)
        return session, agent, scripted

    return make


def assistant_texts(result) -> list[str]:
    return [e.item.text_content for e in result.events
            if getattr(e, "type", None) == "message" and e.item.role == "assistant"]


@pytest.mark.anyio
@pytest.mark.parametrize("attempt", range(10))  # exit criterion: every time, across 10 attempts
async def test_emergency_gets_911_response_and_skips_llm(make_session, backend_calls, attempt):
    session, agent, scripted = await make_session(["You should take an aspirin and lie down."])
    async with session:
        result = await session.run(user_input="my husband is having chest pain and can't breathe")
        await result
    assert scripted.requests == []  # the LLM was never asked
    assert assistant_texts(result) == [EMERGENCY_MESSAGE]
    assert agent.state.urgency == "emergency" and agent.state.outcome == "emergency"
    flags = [b for p, b in backend_calls if p.endswith("/flag")]
    assert flags and flags[0]["urgency"] == "emergency"


@pytest.mark.anyio
async def test_crisis_gets_988(make_session):
    session, _, scripted = await make_session([])
    async with session:
        result = await session.run(user_input="honestly I just want to end my life")
        await result
    assert assistant_texts(result) == [CRISIS_MESSAGE]
    assert scripted.requests == []


@pytest.mark.anyio
async def test_llm_medical_advice_is_replaced(make_session):
    session, agent, _ = await make_session(["We open at eight. For that rash you should apply hydrocortisone and "
                                            "take 25 mg of Benadryl."])
    async with session:
        result = await session.run(user_input="I have a rash, what time do you open?")
        await result
    [reply] = assistant_texts(result)
    assert reply.startswith("We open at eight.")
    assert SAFE_REDIRECT in reply
    assert "mg" not in reply and "Benadryl" not in reply


@pytest.mark.anyio
async def test_clinical_question_adds_reminder_for_llm(make_session):
    session, _, scripted = await make_session(["I can't give medical advice, but I can book you with a doctor."])
    async with session:
        result = await session.run(user_input="should I take ibuprofen for my back?")
        await result
    sent = scripted.requests[0]
    assert any("[guardrail]" in (getattr(i, "text_content", None) or "") for i in sent.items)


@pytest.mark.anyio
async def test_routine_question_goes_to_llm_unchanged(make_session):
    session, _, scripted = await make_session(["There's free parking behind the building."])
    async with session:
        result = await session.run(user_input="where do I park?")
        await result
    assert assistant_texts(result) == ["There's free parking behind the building."]
    system = scripted.requests[0].items[0].text_content
    assert "Maplegate Family Clinic" in system and "/no_think" in system


@pytest.mark.anyio
async def test_turn_limit_ends_call_without_llm(make_session):
    session, agent, scripted = await make_session([])
    agent.state.user_turns = agent.clinic.limits.max_turns  # next turn exceeds the limit
    agent.end_call = lambda goodbye: setattr(agent, "_goodbye", goodbye)  # no room to delete in tests
    async with session:
        result = await session.run(user_input="and another thing")
        await result
    assert scripted.requests == []
    assert "follow up" in agent._goodbye
