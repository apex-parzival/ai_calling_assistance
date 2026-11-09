"""LiveKit voice agent: Deepgram STT -> LLM (OpenAI-compatible) -> Cartesia TTS, Silero VAD.

    python -m core.agent dev        # connect to LiveKit Cloud, hot reload (test in the Playground)
    python -m core.agent console    # talk to it from this machine's mic/speakers
    python -m core.agent start      # production

Voice loop vs side channel (CLAUDE.md §4): nothing in the turn path waits on the
database. Call start/end/summary writes are fire-and-forget; tool calls are the
only backend requests made while the caller waits, and they have short timeouts.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import functools
import logging
import os
import sys
from typing import Any

from livekit import rtc
from livekit.agents import (
    Agent, AgentServer, AgentSession, ChatContext, ChatMessage, JobContext, JobProcess, ModelSettings, RunContext,
    StopResponse, ToolResult, cli, function_tool, llm,
)
from livekit.plugins import silero

from core import phone as phone_utils
from core.backend_client import BackendClient
from core.config import ClinicConfig, ConfigError, load_clinic_config
from core.guardrails import CLINICAL_REMINDER, ReplyFilter, detect_emergency, is_clinical_question
from core.prompts import build_system_prompt
from core.providers.llm import make_llm
from core.providers.stt import make_stt
from core.providers.tts import make_tts
from core.settings import Settings, SettingsError, load_settings, require
from core.summary import summarize
from core.tools import CallState, ReceptionistTools, history_from_items

log = logging.getLogger("agent")

SILENCE_REPROMPT = "Are you still there?"
SILENCE_GOODBYE = "I haven't heard anything, so I'll end the call now. Please call back any time. Goodbye."
TURN_LIMIT_MESSAGE = ("I want to make sure you get the right help, so I'll have someone from the clinic follow up "
                      "with you. Thank you for calling. Goodbye.")
TIME_LIMIT_MESSAGE = ("We've been on the line for a while, so I'll have someone from the clinic call you back. "
                      "Thank you for calling. Goodbye.")


@functools.cache
def config() -> tuple[Settings, ClinicConfig]:
    """Load and validate once per process. Raises before any call is taken if anything is wrong."""
    settings = load_settings()
    require(settings, ["agent"])
    clinic = load_clinic_config(settings.clinic_dir)
    if not clinic.voice.voice_id:
        raise SettingsError(f"voice.voice_id is not set in {settings.clinic_dir / 'clinic.yaml'}")
    return settings, clinic


def _detached(coro) -> asyncio.Task:
    # Fresh contextvars: a task spawned from inside a tool must not inherit the tool's
    # speech context, or waiting on its own speech would deadlock.
    return asyncio.create_task(coro, context=contextvars.Context())


class Receptionist(ReceptionistTools, Agent):
    def __init__(self, clinic: ClinicConfig, settings: Settings, backend: BackendClient, state: CallState,
                 job_ctx: JobContext) -> None:
        self.clinic = clinic
        self.settings = settings
        self.backend = backend
        self.state = state
        self.job_ctx = job_ctx
        self._ending = False
        self._screened: set[str] = set()
        super().__init__(instructions=build_system_prompt(
            clinic, dt.datetime.now(dt.timezone.utc), disable_thinking=settings.llm_disable_thinking))

    # --- conversation hooks -------------------------------------------------

    async def on_enter(self) -> None:
        # Uninterruptible: the AI disclosure and recording notice must be heard in full.
        self.session.say(self.clinic.greeting, allow_interruptions=False)

    def _screen(self, message: ChatMessage) -> tuple[str, str] | None:
        """Run the input guardrails once per caller message.

        Returns ("say", text) for a fixed reply that replaces the LLM, ("end", goodbye) to
        end the call, ("remind", "") to add the clinical reminder, or None.
        Called from both input paths — spoken turns (on_user_turn_completed) and typed or
        programmatic input (which reaches llm_node directly) — so neither can bypass it.
        """
        if message.id in self._screened:
            return None
        self._screened.add(message.id)
        text = message.text_content or ""
        self.state.user_turns += 1

        # Guardrail 1: emergencies never reach the LLM.
        if detection := detect_emergency(text, self.clinic.escalation.extra_urgent_phrases):
            log.warning("emergency language detected (%s): %r", detection.kind, detection.matched)
            self.state.raise_urgency("emergency")
            self.state.outcome = "emergency"
            self.state.reason = self.state.reason or f"Emergency language: {detection.matched}"
            _detached(self.backend.post_or_spool(f"/agent/calls/{self.state.call_id}/flag",
                                                 {"urgency": "emergency", "reason": self.state.reason}))
            return "say", detection.message

        if self.state.user_turns > self.clinic.limits.max_turns:
            log.info("turn limit reached (%d)", self.clinic.limits.max_turns)
            self.state.outcome = self.state.outcome or "turn_limit"
            return "end", TURN_LIMIT_MESSAGE

        # Guardrail 2: remind the model, right before it answers, not to answer clinical questions.
        if is_clinical_question(text):
            return "remind", ""
        return None

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        action = self._screen(new_message)
        if action is None:
            return
        kind, text = action
        if kind == "say":
            self.session.say(text, allow_interruptions=False)
            raise StopResponse()
        if kind == "end":
            self.end_call(text)
            raise StopResponse()
        turn_ctx.add_message(role="system", content=CLINICAL_REMINDER)

    async def llm_node(self, chat_ctx: ChatContext, tools: list[llm.Tool], model_settings: ModelSettings):
        last = chat_ctx.items[-1] if chat_ctx.items else None
        if isinstance(last, ChatMessage) and last.role == "user" and (action := self._screen(last)):
            kind, text = action
            if kind == "say":
                yield text
                return
            if kind == "end":
                self.end_call(text)
                return
            chat_ctx = chat_ctx.copy()
            chat_ctx.add_message(role="system", content=CLINICAL_REMINDER)

        # Guardrail 3: every sentence of every reply is checked before it reaches TTS.
        f = ReplyFilter()
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            if isinstance(chunk, str):
                text = chunk
            elif isinstance(chunk, llm.ChatChunk) and chunk.delta and chunk.delta.content:
                text = chunk.delta.content
                if chunk.delta.tool_calls:
                    yield chunk.model_copy(update={"delta": chunk.delta.model_copy(update={"content": None})})
            else:
                yield chunk  # tool calls, usage, flush markers
                continue
            for sentence in f.feed(text):
                yield sentence + " "
        for sentence in f.flush():
            yield sentence
        if f.blocked:
            log.warning("blocked medical advice in LLM reply (matched %r)", f.blocked)

    # --- helpers used by tools ----------------------------------------------

    def history(self) -> list[tuple[str, str]]:
        return history_from_items(self.session.history.items)

    async def transfer(self, number: str, announcement: str) -> Any:
        if not self.state.sip_identity:
            return ("This is a browser test call with no phone line, so the transfer can't actually happen. Tell "
                    "the caller that on a real call you would connect them to the clinic team now, and offer to "
                    "take a message instead.")

        async def run() -> None:
            handle = self.session.say(announcement, allow_interruptions=False)
            await handle.wait_for_playout()
            try:
                await self.job_ctx.transfer_sip_participant(self.state.sip_identity, f"tel:{number}")
                log.info("transferred caller to %s", number)
            except Exception:
                log.exception("SIP transfer failed")
                self.session.say("I'm sorry, I couldn't connect you. I can take a message and have someone call "
                                 "you back right away. What's your name?")

        _detached(run())
        return ToolResult("Transferring the caller now. Say nothing else.", reply_required=False)

    def end_call(self, goodbye: str) -> None:
        if self._ending:
            return
        self._ending = True

        async def run() -> None:
            handle = self.session.say(goodbye, allow_interruptions=False)
            await handle.wait_for_playout()
            await self.job_ctx.delete_room()

        _detached(run())

    # --- the five tools -----------------------------------------------------

    @function_tool()
    async def check_availability(self, context: RunContext, date: str, service: str) -> str:
        """Look up open appointment times on one day.

        Args:
            date: The day, as YYYY-MM-DD.
            service: The service id from the services list, e.g. standard_visit.
        """
        return await self.do_check_availability(date, service)

    @function_tool()
    async def book_appointment(self, context: RunContext, name: str, phone: str, date: str, time: str,
                               service: str) -> str:
        """Request an appointment. Only call this after reading the details back and the caller saying yes.

        Args:
            name: The caller's full name.
            phone: The caller's 10-digit phone number.
            date: The day, as YYYY-MM-DD.
            time: The time, 24-hour HH:MM, exactly as returned by check_availability.
            service: The service id.
        """
        return await self.do_book_appointment(name, phone, date, time, service)

    @function_tool()
    async def cancel_appointment(self, context: RunContext, phone: str, date: str) -> str:
        """Cancel an appointment. Only call this after reading the phone number and date back and the caller saying yes.

        Args:
            phone: The phone number the appointment is under, 10 digits.
            date: The appointment day, as YYYY-MM-DD.
        """
        return await self.do_cancel_appointment(phone, date)

    @function_tool()
    async def take_message(self, context: RunContext, name: str, phone: str, reason: str) -> str:
        """Leave a message for the front desk. Read the phone number back and get a yes first.

        Args:
            name: The caller's name.
            phone: A 10-digit callback number.
            reason: What the message is about, in a sentence.
        """
        return await self.do_take_message(name, phone, reason)

    @function_tool()
    async def transfer_to_human(self, context: RunContext) -> Any:
        """Transfer the caller to a clinic staff member."""
        await context.wait_for_playout()  # let the agent finish its sentence first
        return await self.do_transfer_to_human()


# --- per-call wiring -------------------------------------------------------

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


def _latency_tracker(state: CallState):
    """Record per-leg timings for every agent reply (CLAUDE.md: measure latency from the start)."""
    last_user: dict[str, float] = {}

    def on_item(ev) -> None:
        item = ev.item
        metrics = getattr(item, "metrics", None) or {}
        if getattr(item, "role", None) == "user":
            last_user.clear()
            last_user.update(metrics)
            return
        if getattr(item, "role", None) != "assistant" or "e2e_latency" not in metrics:
            return
        sample = {
            "turn": len(state.latency) + 1,
            "eou": round(last_user.get("end_of_turn_delay", 0.0), 3),
            "stt": round(last_user.get("transcription_delay", 0.0), 3),
            "llm_ttft": round(metrics.get("llm_node_ttft", 0.0), 3),
            "tts_ttfb": round(metrics.get("tts_node_ttfb", 0.0), 3),
            "total": round(metrics["e2e_latency"], 3),
        }
        state.latency.append(sample)
        log.info("latency turn=%(turn)d total=%(total).2fs eou=%(eou).2fs stt=%(stt).2fs "
                 "llm_ttft=%(llm_ttft).2fs tts_ttfb=%(tts_ttfb).2fs", sample)

    return on_item


def _silence_watcher(session: AgentSession, agent: Receptionist, clinic: ClinicConfig):
    pending: dict[str, asyncio.Task | None] = {"task": None}

    async def hang_up_later() -> None:
        await asyncio.sleep(clinic.limits.silence_hangup_seconds - clinic.limits.silence_reprompt_seconds)
        agent.state.outcome = agent.state.outcome or "silence"
        agent.end_call(SILENCE_GOODBYE)

    def on_user_state(ev) -> None:
        if ev.new_state == "away" and pending["task"] is None and not agent._ending:
            session.say(SILENCE_REPROMPT)
            pending["task"] = asyncio.create_task(hang_up_later())
        elif ev.new_state == "speaking" and pending["task"] is not None:
            pending["task"].cancel()
            pending["task"] = None

    return on_user_state


def _transcript(session: AgentSession) -> list[dict]:
    out = []
    for item in session.history.items:
        role, text = getattr(item, "role", None), getattr(item, "text_content", None)
        if role in ("user", "assistant") and text:
            at = dt.datetime.fromtimestamp(item.created_at, dt.timezone.utc).isoformat()
            out.append({"role": role, "text": text, "at": at})
    return out


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    settings, clinic = config()
    state = CallState(room=ctx.room.name)
    backend = BackendClient(settings, room=ctx.room.name)

    await ctx.connect()
    participant = await ctx.wait_for_participant()
    if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        state.sip_identity = participant.identity
        # Caller ID from the SIP From header; forwarded calls still show the patient's number.
        state.caller_phone = phone_utils.normalize(participant.attributes.get("sip.phoneNumber"))
    log.info("call started room=%s sip=%s", ctx.room.name, bool(state.sip_identity))

    started = _detached(backend.post_or_spool("/agent/calls", {
        "id": str(state.call_id), "room_name": state.room, "caller_phone": state.caller_phone,
        "started_at": state.started_at.isoformat(),
    }))

    agent = Receptionist(clinic, settings, backend, state, ctx)
    session = AgentSession(
        stt=make_stt(clinic, settings),
        llm=make_llm(settings),
        tts=make_tts(clinic, settings),
        vad=ctx.proc.userdata["vad"],
        user_away_timeout=clinic.limits.silence_reprompt_seconds,
        max_tool_steps=3,
    )
    session.on("conversation_item_added", _latency_tracker(state))
    session.on("user_state_changed", _silence_watcher(session, agent, clinic))

    async def call_time_limit() -> None:
        await asyncio.sleep(clinic.limits.max_call_seconds)
        state.outcome = state.outcome or "time_limit"
        agent.end_call(TIME_LIMIT_MESSAGE)

    limit_task = asyncio.create_task(call_time_limit())

    async def finalize(reason: str = "") -> None:
        limit_task.cancel()
        await started  # keep start -> end ordering, including when spooled
        transcript = _transcript(session)
        for msg in state.unsent_messages:
            if msg.get("unvalidated"):
                transcript.append({"role": "system", "text": f"[unvalidated message] {msg}"})
            else:
                await backend.post_or_spool("/agent/messages", msg)
        await backend.post_or_spool(f"/agent/calls/{state.call_id}/end", {
            "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "caller_name": state.caller_name, "caller_phone": state.caller_phone, "reason": state.reason,
            "urgency": state.urgency, "outcome": state.outcome, "transcript": transcript, "latency": state.latency,
        })
        log.info("call ended room=%s turns=%d outcome=%s", state.room, state.user_turns, state.outcome)
        # Summary after the record is saved, so the dashboard shows the call immediately.
        if result := await summarize(settings, transcript):
            summary, reason_text = result
            await backend.post_or_spool(f"/agent/calls/{state.call_id}/summary",
                                        {"summary": summary, "reason": state.reason or reason_text})
        await backend.aclose()

    ctx.add_shutdown_callback(finalize)
    # record=False: no audio or transcripts uploaded to LiveKit Cloud. Our own backend keeps the
    # transcript; call recordings need explicit consent handling first (Phase 4).
    await session.start(agent=agent, room=ctx.room, record=False)


def main() -> None:
    try:
        settings, _ = config()
    except (SettingsError, ConfigError) as e:
        print(f"agent cannot start:\n{e}", file=sys.stderr)
        raise SystemExit(2) from None
    # The LiveKit worker reads its credentials from the environment.
    os.environ.setdefault("LIVEKIT_URL", settings.livekit_url)
    os.environ.setdefault("LIVEKIT_API_KEY", settings.livekit_api_key)
    os.environ.setdefault("LIVEKIT_API_SECRET", settings.livekit_api_secret.get_secret_value())
    cli.run_app(server)


if __name__ == "__main__":
    main()
