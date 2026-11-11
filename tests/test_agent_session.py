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

    async def make(replies: list[str], warmup_llm=None, scripted: ScriptedLLM | None = None):
        scripted = scripted or ScriptedLLM(replies)
        backend = BackendClient(settings, spool_dir=tmp_path / "spool", transport=httpx.MockTransport(handler))
        state = CallState()
        agent = Receptionist(clinic, settings, backend, state, job_ctx=None, warmup_llm=warmup_llm)
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
async def test_repeated_emergency_answered_each_time_but_flagged_once(make_session, backend_calls):
    # On a live call the same words are also screened on LiveKit's preemptive (non-final)
    # transcript; staff must get one alert, and every committed message still gets the 911 text.
    session, agent, scripted = await make_session([])
    async with session:
        first = await session.run(user_input="my dad just collapsed and he's not breathing")
        await first
        second = await session.run(user_input="he's still not breathing")
        await second
    assert assistant_texts(first) == [EMERGENCY_MESSAGE]
    assert assistant_texts(second) == [EMERGENCY_MESSAGE]
    assert scripted.requests == []
    assert agent.state.user_turns == 2
    assert len([p for p, _ in backend_calls if p.endswith("/flag")]) == 1


@pytest.mark.anyio
async def test_preemptive_transcript_never_ends_call_or_counts_turn(make_session):
    from livekit.agents.llm import ChatContext, ChatMessage
    session, agent, _ = await make_session(["Okay."])
    agent.state.user_turns = agent.clinic.limits.max_turns
    ended = []
    agent.end_call = ended.append
    async with session:
        ctx = ChatContext()
        ctx.add_message(role="user", content="one more question")  # not in session.history
        out = [c async for c in agent.llm_node(ctx, [], None)]
    assert out == [] and ended == []  # nothing said, call not ended on a speculative transcript
    assert agent.state.user_turns == agent.clinic.limits.max_turns


class _FailingStream(llm.LLMStream):
    async def _run(self) -> None:
        from livekit.agents import APIStatusError
        raise APIStatusError("Payment Required", status_code=402, retryable=False)


class FailingLLM(ScriptedLLM):
    def chat(self, *, chat_ctx, tools=None, conn_options=DEFAULT_API_CONNECT_OPTIONS, **_kw):
        self.requests.append(chat_ctx.copy())
        return _FailingStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


@pytest.mark.anyio
async def test_llm_failure_is_never_silence(clean_env, clinics_root, tmp_path):
    # Seen on a real call: the LLM provider returned 402 and the caller heard nothing.
    import asyncio
    for k, v in VALID_ENV.items():
        clean_env.setenv(k, v)
    settings = load_settings(clinics_root=clinics_root)
    clinic = load_clinic_config(settings.clinic_dir)
    backend = BackendClient(settings, spool_dir=tmp_path / "spool",
                            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    agent = Receptionist(clinic, settings, backend, CallState(), job_ctx=None)
    goodbyes = []
    agent.end_call = goodbyes.append
    session = AgentSession(llm=FailingLLM([]))
    await session.start(agent)
    said = []
    session.on("conversation_item_added",
               lambda ev: said.append(ev.item.text_content) if ev.item.role == "assistant" else None)
    async with session:
        result = await session.run(user_input="what time do you open?")
        try:
            await result
        except Exception:
            pass
        await asyncio.sleep(0.5)
    fallback = [s for s in said if "technical problem" in (s or "")]
    assert fallback, said
    assert "5 5 5" in fallback[0]  # the clinic's number, read out digit by digit
    assert agent.state.outcome == "system_error"


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


@pytest.mark.anyio
async def test_detached_task_keeps_context_but_not_speech_handle():
    """end_call runs in a detached task; it must still see LiveKit's HTTP session (delete_room
    needs it) but not the current speech handle (waiting on its own speech would deadlock)."""
    import contextvars

    from livekit.agents.voice.agent_activity import _SpeechHandleContextVar

    from core.agent import _detached

    other = contextvars.ContextVar("other_for_test")
    other.set("http-session")
    _SpeechHandleContextVar.set(object())

    async def look():
        return other.get(None), _SpeechHandleContextVar.get(None)

    assert await _detached(look()) == ("http-session", None)


class _ToolCallLLM(ScriptedLLM):
    """First request: optional words, then a check_availability call. Later requests: an answer."""

    def __init__(self, preamble: str = "") -> None:
        super().__init__(["We have nine a.m. open."])
        self.preamble = preamble

    def chat(self, *, chat_ctx, tools=None, conn_options=DEFAULT_API_CONNECT_OPTIONS, **kw):
        if self.requests:
            return super().chat(chat_ctx=chat_ctx, tools=tools, conn_options=conn_options, **kw)
        self.requests.append(chat_ctx.copy())
        return _ToolCallStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options,
                               text=self.preamble)


class _ToolCallStream(_ScriptedStream):
    async def _run(self) -> None:
        await super()._run()
        self._event_ch.send_nowait(llm.ChatChunk(id="s", delta=llm.ChoiceDelta(role="assistant", tool_calls=[
            llm.FunctionToolCall(name="check_availability", call_id="c1",
                                 arguments='{"date": "2026-11-20", "service": "standard_visit"}')])))


async def _spoken_during(session, user_input: str) -> list[str]:
    spoken: list[str] = []
    real_say = session.say

    def say(text, **kw):
        spoken.append(text)
        return real_say(text, **kw)

    session.say = say
    result = await session.run(user_input=user_input)
    await result
    return spoken


@pytest.mark.anyio
async def test_silent_tool_call_gets_a_hold_on_line(make_session):
    from core.agent import HOLD_ON

    session, agent, model = await make_session([], scripted=_ToolCallLLM())
    async with session:
        spoken = await _spoken_during(session, "anything open on the twentieth?")
    assert len(model.requests) == 2  # the tool ran and the model answered from its result
    assert spoken == [HOLD_ON["check_availability"]]
    # never in the model's history, so it can't echo it or treat it as said
    assert HOLD_ON["check_availability"] not in [getattr(i, "text_content", None) for i in agent.chat_ctx.items]


@pytest.mark.anyio
async def test_no_hold_on_line_when_the_model_already_spoke(make_session):
    session, _, model = await make_session([], scripted=_ToolCallLLM(preamble="Let me look at the twentieth."))
    async with session:
        spoken = await _spoken_during(session, "anything open on the twentieth?")
    assert len(model.requests) == 2
    assert spoken == []


class _RecordingWarmup(ScriptedLLM):
    def __init__(self) -> None:
        super().__init__(["Okay."])
        self.tools: list = []

    def chat(self, *, chat_ctx, tools=None, **kw):
        self.tools.append(list(tools or []))
        return super().chat(chat_ctx=chat_ctx, tools=tools, **kw)


@pytest.mark.anyio
async def test_llm_cache_warmed_with_the_real_prompt_and_tools(make_session):
    import asyncio

    warm = _RecordingWarmup()
    session, agent, scripted = await make_session(["We open at 8."], warmup_llm=warm)
    async with session:
        await asyncio.sleep(0.2)  # the warm-up runs alongside the greeting
        result = await session.run(user_input="what time do you open?")
        await result
    assert len(warm.requests) == 1
    system = [m for m in warm.requests[0].items if getattr(m, "role", None) == "system"]
    assert system and agent.clinic.name in system[0].text_content
    names = {getattr(t, "id", None) or getattr(t, "name", None) for t in warm.tools[0]}
    assert {"book_appointment", "take_message", "transfer_to_human"} <= names
    # the real request starts with the same system prompt the warm-up sent
    real_system = [m for m in scripted.requests[0].items if getattr(m, "role", None) == "system"]
    assert real_system[0].text_content == system[0].text_content


@pytest.mark.anyio
async def test_failed_warm_up_never_sets_off_llm_down_fallback(make_session):
    import asyncio

    failing = FailingLLM([])
    session, agent, _ = await make_session(["We open at 8."], warmup_llm=failing)
    said = []
    session.on("conversation_item_added",
               lambda ev: said.append(ev.item.text_content) if ev.item.role == "assistant" else None)
    async with session:
        await asyncio.sleep(0.3)
        result = await session.run(user_input="what time do you open?")
        await result
    assert len(failing.requests) == 1  # the warm-up ran and failed
    assert agent.state.failures["llm"] == 0
    assert agent.state.outcome != "system_error"
    assert not [t for t in said if "technical problem" in (t or "")]
    assert any("We open at 8." in (t or "") for t in said)
