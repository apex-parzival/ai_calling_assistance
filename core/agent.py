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
import re
import sys
import time
from typing import Any

from livekit import rtc
from livekit.agents import (
    Agent, AgentServer, AgentSession, APIConnectOptions, ChatContext, ChatMessage, JobContext, JobProcess,
    ModelSettings, RunContext, SessionConnectOptions, StopResponse, ToolResult, cli, function_tool, llm,
)
from livekit.plugins import silero

from core import phone as phone_utils
from core.backend_client import BackendClient
from core.config import ClinicConfig, ConfigError, load_clinic_config
from core.guardrails import CLINICAL_REMINDER, ReplyFilter, detect_emergency, is_clinical_question
from core.prompts import build_intake_prompt, build_system_prompt  # noqa: F401 - full prompt kept for switching back
from core.providers.llm import make_llm
from core.providers.stt import make_stt
from core.providers.tts import make_tts
from core.settings import Settings, SettingsError, load_settings, require
from core.summary import summarize
from core.tools import CallState, ReceptionistTools, history_from_items, unkept_follow_up

log = logging.getLogger("agent")

LLM_DOWN_GOODBYE = ("I'm sorry, I'm still having a technical problem. The clinic will see that you called. "
                    "Goodbye.")
SILENCE_REPROMPT = "Are you still there?"
SAY_AGAIN = "Sorry, could you say that again?"
SILENCE_GOODBYE = "I haven't heard anything, so I'll end the call now. Please call back any time. Goodbye."
TURN_LIMIT_MESSAGE = ("I want to make sure you get the right help, so I'll have someone from the clinic follow up "
                      "with you. Thank you for calling. Goodbye.")
TIME_LIMIT_MESSAGE = ("We've been on the line for a while, so I'll have someone from the clinic call you back. "
                      "Thank you for calling. Goodbye.")
# Said when a tool starts and the model hasn't said anything this step, so the caller hears
# something while the tool runs and the model writes its answer (otherwise 1-2 s of silence).
HOLD_ON = {
    "check_availability": "Let me check that for you.",
    "book_appointment": "One moment while I put that in.",
    "cancel_appointment": "One moment.",
    "take_message": "One moment while I write that down.",
}


@functools.cache
def config() -> tuple[Settings, ClinicConfig]:
    """Load and validate once per process. Raises before any call is taken if anything is wrong."""
    settings = load_settings()
    clinic = load_clinic_config(settings.clinic_dir)
    require(settings, ["agent"], tts_provider=clinic.voice.tts_provider)
    if clinic.voice.tts_provider == "cartesia" and not clinic.voice.voice_id:
        # Kokoro falls back to KOKORO_VOICE; a Cartesia voice is an account-specific ID.
        raise SettingsError(f"voice.voice_id is not set in {settings.clinic_dir / 'clinic.yaml'}")
    return settings, clinic


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _detached(coro) -> asyncio.Task:
    # A task spawned from inside a tool must not inherit the tool's speech handle, or waiting
    # on its own speech would deadlock. Everything else in the context is kept: LiveKit's HTTP
    # session lives in a context variable, and without it ending the call (delete_room) fails
    # and the caller is left on a silent line.
    ctx = contextvars.copy_context()
    try:
        from livekit.agents.voice.agent_activity import _SpeechHandleContextVar
        ctx.run(_SpeechHandleContextVar.set, None)
    except ImportError:  # private name; if LiveKit renames it, fall back to an empty context
        ctx = contextvars.Context()
    return asyncio.create_task(coro, context=ctx)


class Receptionist(ReceptionistTools, Agent):
    def __init__(self, clinic: ClinicConfig, settings: Settings, backend: BackendClient, state: CallState,
                 job_ctx: JobContext, warmup_llm: llm.LLM | None = None) -> None:
        self.clinic = clinic
        self._warmup_llm = warmup_llm
        self.settings = settings
        self.backend = backend
        self.state = state
        self.job_ctx = job_ctx
        self._ending = False
        self._screened: set[str] = set()
        self._flagged = False  # staff alerted about an emergency on this call
        self._pending_end: str | None = None  # turn-limit goodbye waiting for the message to commit
        self._spoke_this_step = False  # the current LLM step has produced text for the caller
        self._greeting_sentences: set[str] | None = None
        self._instruction_text: str | None = None
        self._example_replies: list[str] = []
        # FULL RECEPTIONIST prompt (FAQs, booking, cancelling, messages, transfers). To switch back, use this
        # and uncomment the five tools at the bottom of this class.
        # super().__init__(instructions=build_system_prompt(
        #     clinic, dt.datetime.now(dt.timezone.utc), disable_thinking=settings.llm_disable_thinking))
        # Intake flow: what the caller wants, their name and number, then "the front desk will call you back".
        super().__init__(instructions=build_intake_prompt(
            clinic, dt.datetime.now(dt.timezone.utc), caller_phone=state.caller_phone,
            disable_thinking=settings.llm_disable_thinking))

    # --- conversation hooks -------------------------------------------------

    async def on_enter(self) -> None:
        self.session.on("conversation_item_added", self._count_turn)
        self.session.on("error", self._on_error)
        # Uninterruptible: the AI disclosure and recording notice must be heard in full.
        self.session.say(self.clinic.greeting, allow_interruptions=False)
        if self._warmup_llm is not None:
            _detached(self._warm_llm_cache())

    async def _warm_llm_cache(self) -> None:
        """Make the LLM read the system prompt and tool list while the greeting plays.

        The first reply of a call otherwise pays for reading the whole prompt (~1.6 s on local
        Ollama). The provider keeps what it read (Ollama/vLLM prefix cache, hosted prompt caching),
        and every real request of the call starts with exactly these messages and tools, so the
        first answer only has to read the caller's words. The request stops at its first token.

        Uses its own LLM client (same settings), so a failure here can't reach the session's
        error handling and set off the "LLM is down" fallback. It only ever costs latency.
        """
        started = time.perf_counter()
        try:
            async with asyncio.timeout(15):
                tools = self._get_activity_or_raise().tools
                async with self._warmup_llm.chat(
                        chat_ctx=self.chat_ctx.copy(), tools=tools,
                        conn_options=APIConnectOptions(max_retry=0, timeout=15)) as stream:
                    async for _ in stream:
                        break  # first token = the prompt has been read
            log.info("llm prompt cache warmed in %.2fs", time.perf_counter() - started)
        except Exception as e:  # noqa: BLE001 - never let a warm-up affect the call
            log.warning("llm warm-up skipped (%s: %s); first reply will just be slower", type(e).__name__, e)

    def _on_error(self, ev) -> None:
        """The LLM failed for good (retries used up): never leave the caller in silence."""
        err = getattr(ev, "error", None)
        if getattr(err, "type", None) != "llm_error" or getattr(err, "recoverable", True):
            return
        self.state.failures["llm"] += 1
        if self.state.outcome in (None, "answered"):  # don't hide an emergency or a booking
            self.state.outcome = "system_error"
        log.error("LLM unavailable (%d): %s", self.state.failures["llm"], getattr(err, "error", err))
        if self.state.failures["llm"] >= 2:
            self.end_call(LLM_DOWN_GOODBYE)
        elif self.state.sip_identity:
            # A real phone line: hand the caller to a person rather than keep failing.
            esc = self.clinic.escalation
            _detached(self.transfer(esc.transfer_number, esc.transfer_announcement))
        else:
            self.session.say(
                "I'm sorry, I'm having a technical problem right now. Please call the clinic directly at "
                f"{phone_utils.spoken(self.clinic.main_phone)}, or try again in a few minutes.",
                allow_interruptions=False)

    def _count_turn(self, ev) -> None:
        if getattr(ev.item, "role", None) == "user":
            self.state.user_turns += 1
            if self._pending_end:
                goodbye, self._pending_end = self._pending_end, None
                self.end_call(goodbye)

    def _screen(self, message: ChatMessage, *, committed: bool) -> tuple[str, str] | None:
        """Run the input guardrails on a caller message.

        Returns ("say", text) for a fixed reply that replaces the LLM, ("end", goodbye) to
        end the call, ("remind", "") to add the clinical reminder, or None.

        Reached three ways, and must hold on all of them:
        - spoken final turn: on_user_turn_completed, before LiveKit commits the message;
        - typed / programmatic input: llm_node, message already committed to the history;
        - preemptive generation: llm_node on a not-yet-final transcript, which LiveKit throws
          away when the final turn arrives. So side effects here are idempotent (flag once),
          and turns are counted from committed messages (_count_turn), not here.
        """
        if message.id in self._screened:
            return None
        if committed:
            self._screened.add(message.id)
        text = message.text_content or ""

        # Guardrail 1: emergencies never reach the LLM.
        if detection := detect_emergency(text, self.clinic.escalation.extra_urgent_phrases):
            log.warning("emergency language detected (%s): %r", detection.kind, detection.matched)
            self.state.raise_urgency("emergency")
            self.state.outcome = "emergency"
            self.state.reason = self.state.reason or f"Emergency language: {detection.matched}"
            if not self._flagged:
                self._flagged = True
                _detached(self.backend.post_or_spool(f"/agent/calls/{self.state.call_id}/flag",
                                                     {"urgency": "emergency", "reason": self.state.reason}))
            return "say", detection.message

        turns = self.state.user_turns + (0 if committed else 1)  # including this message
        if turns > self.clinic.limits.max_turns:
            log.info("turn limit reached (%d)", self.clinic.limits.max_turns)
            self.state.outcome = self.state.outcome or "turn_limit"
            return "end", TURN_LIMIT_MESSAGE

        # Guardrail 2: remind the model, right before it answers, not to answer clinical questions.
        if is_clinical_question(text):
            return "remind", ""
        return None

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        action = self._screen(new_message, committed=False)
        self._screened.add(new_message.id)  # final spoken turn: llm_node mustn't screen it again
        if action is None:
            return
        kind, text = action
        if kind in ("say", "end"):
            # StopResponse means LiveKit won't commit the caller's words, so the transcript (and
            # staff) would never see what triggered the emergency. Keep them, and count the turn.
            if self.session.history.get_by_id(new_message.id) is None:
                self.session.history.insert(new_message)
                self.state.user_turns += 1
            if kind == "say":
                self.session.say(text, allow_interruptions=False)
            else:
                self.end_call(text)
            raise StopResponse()
        turn_ctx.add_message(role="system", content=CLINICAL_REMINDER)

    async def llm_node(self, chat_ctx: ChatContext, tools: list[llm.Tool], model_settings: ModelSettings):
        last = chat_ctx.items[-1] if chat_ctx.items else None
        committed = isinstance(last, ChatMessage) and self.session.history.get_by_id(last.id) is not None
        if isinstance(last, ChatMessage) and last.role == "user" and (
                action := self._screen(last, committed=committed)):
            kind, text = action
            if kind == "say":
                yield text
                return
            if kind == "end":
                if committed:
                    self.end_call(text)
                else:
                    # Typed input is committed just after this; a preemptive transcript never is
                    # (its final turn ends the call in on_user_turn_completed). End on commit.
                    self._pending_end = text
                return
            chat_ctx = chat_ctx.copy()
            chat_ctx.add_message(role="system", content=CLINICAL_REMINDER)

        # Guardrail 3: every sentence of every reply is checked before it reaches TTS.
        f = ReplyFilter()
        dropped: list[str] = []
        said = leaked = False
        self._spoke_this_step = False
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
            # Set on arrival, not on emit: the filter holds a sentence back until it ends, and
            # a tool can start before that ("Let me check" + tool call). No hold-on line then.
            self._spoke_this_step |= bool(text.strip())
            for sentence in f.feed(text):
                if self._reads_instructions(sentence):
                    leaked = True
                elif self._repeats_greeting(sentence):
                    dropped.append(sentence)
                else:
                    said = True
                    yield sentence + " "
        for sentence in f.flush():
            if self._reads_instructions(sentence):
                leaked = True
            elif self._repeats_greeting(sentence):
                dropped.append(sentence)
            else:
                said = True
                yield sentence
        if leaked:
            log.warning("dropped instruction text from LLM reply")
        if not said and dropped:
            yield dropped[-1]  # the whole reply was the greeting again: still answer, never silence
        elif not said and leaked:
            yield SAY_AGAIN
        if f.blocked:
            log.warning("blocked medical advice in LLM reply (matched %r)", f.blocked)

    def _repeats_greeting(self, sentence: str) -> bool:
        """Small models sometimes start every reply with the greeting again; the caller heard it once."""
        if self._greeting_sentences is None:
            sentences = {_norm(s) for s in re.split(r"(?<=[.!?])\s+", self.clinic.greeting)}
            # Never filter emergency wording: the model may say it to a caller in danger.
            self._greeting_sentences = {s for s in sentences if s and not re.search(r"9 ?1 ?1|emergenc", s)}
        return _norm(sentence) in self._greeting_sentences

    def _reads_instructions(self, sentence: str) -> bool:
        """True if the model is reading its own instructions aloud (seen with an 8B model).

        A sentence of five or more words copied from the system prompt or the clinical reminder,
        unless it's one of the quoted example replies the prompt gives the model to say, or clinic
        information from clinic.yaml (FAQ answers, directions...) that is meant to be read out.
        """
        if self._instruction_text is None:
            text = f"{self.instructions}\n{CLINICAL_REMINDER}"
            c = self.clinic
            quoted = [q for q in re.findall(r'"([^"]*)"', text) if len(q) >= 8]  # pair quotes first, then filter
            speakable = [*quoted, *(f.answer for f in c.faqs), c.address.directions,
                         c.hours_notes, *(f"{s.description} {s.notes}" for s in c.services),
                         ", ".join(s.name for s in c.services)]
            self._instruction_text = _norm(text)
            self._example_replies = [_norm(t) for t in speakable if t]
        s = _norm(sentence)
        return (len(s.split()) >= 5 and s in self._instruction_text
                and not any(s in ex for ex in self._example_replies))

    # --- helpers used by tools ----------------------------------------------

    def history(self) -> list[tuple[str, str]]:
        return history_from_items(self.session.history.items)

    def _hold_on(self, tool: str) -> None:
        """Fill the silence while a tool runs, unless the model already spoke this step.

        Not awaited and not added to the chat history (the model never sees it, so it can't
        repeat or build on it). LiveKit queues it ahead of the tool's reply, which is being
        generated meanwhile, so it costs no time.
        """
        if self._spoke_this_step or self._ending:
            return
        self._spoke_this_step = True
        try:
            self.session.say(HOLD_ON[tool], add_to_chat_ctx=False)
        except RuntimeError:  # session closing; the reply itself will fail the same way
            pass

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

    # --- intake flow: the one tool ---------------------------------------------

    @function_tool()
    async def save_callback_request(self, context: RunContext, name: str, phone: str, request_type: str,
                                    details: str) -> Any:
        """Save the caller's request so the front desk calls them back. Only call this after reading the phone
        number back and the caller saying it is correct. The system then says goodbye and ends the call.

        Args:
            name: The caller's full name.
            phone: The 10-digit callback number the caller confirmed.
            request_type: One of: appointment, health_question, other.
            details: For an appointment, the service. Otherwise what it is about, in a few words.
        """
        saved, result = await self.do_save_callback_request(name, phone, request_type, details)
        if not saved:
            return result  # a correction for the model; nothing was saved
        # Fixed wording from clinic.yaml, then hang up, so the model can't promise anything else.
        self.end_call(self.clinic.callback_message)
        return ToolResult("Saved. The closing message is playing and the call is ending. Say nothing else.",
                          reply_required=False)

    # --- FULL RECEPTIONIST: the five tools ------------------------------------
    # Turned off for the intake flow. To switch back: uncomment these five tools, use build_system_prompt
    # in __init__ above, restore the full-flow texts in core/guardrails.py and the greeting in clinic.yaml.
    # Their logic (do_check_availability, do_book_appointment, ...) is still live in core/tools.py and tested.
    #
    # @function_tool()
    # async def check_availability(self, context: RunContext, date: str, service: str) -> str:
    #     """Look up open appointment times on one day.
    #
    #     Args:
    #         date: The day, as YYYY-MM-DD.
    #         service: The service id from the services list, e.g. standard_visit.
    #     """
    #     self._hold_on("check_availability")
    #     return await self.do_check_availability(date, service)
    #
    # @function_tool()
    # async def book_appointment(self, context: RunContext, name: str, phone: str, date: str, time: str,
    #                            service: str) -> str:
    #     """Request an appointment. Only call this after reading the details back and the caller saying yes.
    #
    #     Args:
    #         name: The caller's full name.
    #         phone: The caller's 10-digit phone number.
    #         date: The day, as YYYY-MM-DD.
    #         time: The time, 24-hour HH:MM, exactly as returned by check_availability.
    #         service: The service id.
    #     """
    #     self._hold_on("book_appointment")
    #     return await self.do_book_appointment(name, phone, date, time, service)
    #
    # @function_tool()
    # async def cancel_appointment(self, context: RunContext, phone: str, date: str) -> str:
    #     """Cancel an appointment. Only call this after reading the phone number and date back and the caller saying yes.
    #
    #     Args:
    #         phone: The phone number the appointment is under, 10 digits.
    #         date: The appointment day, as YYYY-MM-DD.
    #     """
    #     self._hold_on("cancel_appointment")
    #     return await self.do_cancel_appointment(phone, date)
    #
    # @function_tool()
    # async def take_message(self, context: RunContext, name: str, phone: str, reason: str) -> str:
    #     """Leave a message for the front desk. Read the phone number back and get a yes first.
    #
    #     Args:
    #         name: The caller's name.
    #         phone: A 10-digit callback number.
    #         reason: What the message is about, in a sentence.
    #     """
    #     self._hold_on("take_message")
    #     return await self.do_take_message(name, phone, reason)
    #
    # @function_tool()
    # async def transfer_to_human(self, context: RunContext) -> Any:
    #     """Transfer the caller to a clinic staff member."""
    #     await context.wait_for_playout()  # let the agent finish its sentence first
    #     return await self.do_transfer_to_human()


# --- per-call wiring -------------------------------------------------------

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    _, clinic = config()
    # Silero only reports end of speech after this much silence, so it bounds the turn-end wait
    # whatever the endpointing delay is; the two are set together (clinic.yaml voice.end_of_turn_silence).
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=clinic.voice.end_of_turn_silence)
    # Otherwise imported lazily on the first LLM call, blocking the event loop ~0.4 s mid-call.
    import openai.types.beta  # noqa: F401


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

    agent = Receptionist(clinic, settings, backend, state, ctx,
                         warmup_llm=make_llm(settings) if settings.llm_warmup else None)
    session = AgentSession(
        stt=make_stt(clinic, settings),
        llm=make_llm(settings),
        tts=make_tts(clinic, settings),
        vad=ctx.proc.userdata["vad"],
        user_away_timeout=clinic.limits.silence_reprompt_seconds,
        turn_handling={"endpointing": {"min_delay": clinic.voice.end_of_turn_silence}},
        max_tool_steps=3,
        # A caller won't sit through silence: give up on a stalled LLM request after 4 s and
        # retry at once (default is 10 s, then a 2 s pause). A real call once stalled 19 s.
        conn_options=SessionConnectOptions(
            llm_conn_options=APIConnectOptions(max_retry=2, retry_interval=0.1, timeout=4.0)),
    )
    session.on("conversation_item_added", _latency_tracker(state))
    session.on("user_state_changed", _silence_watcher(session, agent, clinic))

    async def call_time_limit() -> None:
        await asyncio.sleep(clinic.limits.max_call_seconds)
        state.outcome = state.outcome or "time_limit"
        agent.end_call(TIME_LIMIT_MESSAGE)

    limit_task = asyncio.create_task(call_time_limit())

    finalized = False

    async def finalize(reason: str = "") -> None:
        nonlocal finalized
        if finalized:
            return
        finalized = True
        limit_task.cancel()
        await started  # keep start -> end ordering, including when spooled
        transcript = _transcript(session)
        if unkept_follow_up(history_from_items(session.history.items), state.outcome) and not state.unsent_messages:
            log.warning("agent promised a follow-up but nothing was recorded; flagging call %s", state.call_id)
            state.outcome = "follow_up_promised"
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
    # Save the call the moment the session ends (caller hung up, or we ended it). Otherwise the
    # job waits for LiveKit to close the empty room (~20 s), and the dashboard hears late.
    session.on("close", lambda ev: ctx.shutdown(reason=f"session closed: {getattr(ev, 'reason', '')}"))

    # Faster still: the caller leaving is the hang-up. Closing the session takes ~2 s more.
    def on_participant_left(p: rtc.RemoteParticipant) -> None:
        if p.identity == participant.identity:
            _detached(finalize("caller hung up"))

    ctx.room.on("participant_disconnected", on_participant_left)
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
