"""The five LLM function tools. Each one calls the FastAPI backend over HTTP.

Reliability rules (CLAUDE.md §7), enforced here in code:
- Every payload is validated against core.tool_schemas before anything happens.
  First failure: the LLM gets a correction and may retry. Second failure on the
  same tool: it's told to fall back to take_message.
- book / cancel / take_message refuse to run until the details were read back
  to the caller and the caller said yes (checked against the conversation).
- A backend outage never strands the caller: the LLM is told to offer a message,
  and messages themselves are kept locally and spooled if the backend is down.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from core import tool_schemas as ts
from core.backend_client import BackendClient, BackendError, BackendRejected
from core.config import ClinicConfig

log = logging.getLogger("agent.tools")

FALLBACK_TO_MESSAGE = (
    "This isn't working. Apologise briefly and offer to take a message instead: get the caller's name, phone "
    "number and reason, read the number back, then call take_message."
)
SYSTEM_DOWN = (
    "The clinic system is not responding right now. Apologise and offer to take a message so the front desk "
    "can call the caller back."
)


@dataclass
class CallState:
    """Everything the agent learns during one call. Sent to the backend at hang-up."""

    call_id: uuid.UUID = field(default_factory=uuid.uuid4)
    room: str = ""
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    caller_phone: str | None = None   # from SIP caller ID, when there is one
    caller_name: str | None = None
    reason: str | None = None
    urgency: str = "routine"
    outcome: str | None = None
    sip_identity: str | None = None   # set when the caller is on a real phone line
    user_turns: int = 0
    failures: Counter = field(default_factory=Counter)
    unsent_messages: list[dict] = field(default_factory=list)
    latency: list[dict] = field(default_factory=list)

    def raise_urgency(self, level: str) -> None:
        order = ["routine", "urgent", "emergency"]
        if order.index(level) > order.index(self.urgency):
            self.urgency = level


# --- read-back confirmation -------------------------------------------------

_AFFIRM = re.compile(
    r"\b(yes|yeah|yep|yup|correct|that'?s (?:right|correct)|right|sure|sounds good|perfect|please do|go ahead|"
    r"ok(?:ay)?|exactly|uh[- ]?huh|mm[- ]?hmm|absolutely|that works)\b", re.IGNORECASE)
_DENY = re.compile(r"\b(no|nope|not|wrong|incorrect|wait|actually|hold on|change)\b", re.IGNORECASE)
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]


_DIGIT_WORDS = re.compile(r"\b(zero|oh|one|two|three|four|five|six|seven|eight|nine)\b", re.IGNORECASE)
_WORD_VALUE = {w: str(i) for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven",
                                                 "eight", "nine"])} | {"oh": "0"}


def _digits(text: str) -> str:
    """All digits in order, counting spelled-out ones ("four oh three" -> "403")."""
    text = _DIGIT_WORDS.sub(lambda m: _WORD_VALUE[m.group(1).lower()], text)
    return "".join(ch for ch in text if ch.isdigit())


def _is_yes(text: str) -> bool:
    return bool(_AFFIRM.search(text)) and not _DENY.search(text)


def phone_confirmed_earlier(history: list[tuple[str, str]], phone: str) -> bool:
    """True if, anywhere in the call, the agent said this number and the caller's next turn was a yes.

    Enough for take_message, where only the number has to be right: the caller often confirms it
    and then explains the reason. A corrected number won't match, so a stale confirmation can't count.
    """
    turns = [(r, t) for r, t in history if t and r in ("user", "assistant")]
    local = phone.removeprefix("+1")
    return any(r == "assistant" and local in _digits(t) and nr == "user" and _is_yes(nt)
               for (r, t), (nr, nt) in zip(turns, turns[1:]))


def readback_problem(history: list[tuple[str, str]], *, phone: str | None = None, date: dt.date | None = None,
                     time: dt.time | None = None) -> str | None:
    """Return what's missing, or None if the agent read the details back and the caller then said yes.

    `history` is [(role, text), ...] oldest first, containing only user/assistant turns.
    """
    turns = [(r, t) for r, t in history if t and r in ("user", "assistant")]
    if not turns or turns[-1][0] != "user":
        return "the caller hasn't confirmed yet"
    if not _is_yes(turns[-1][1]):
        return "the caller's last answer wasn't a clear yes"
    # The read-back is the last one or two assistant turns before the confirmation.
    said = " ".join(t for r, t in turns[-3:-1] if r == "assistant")
    if not said:
        return "nothing was read back"
    missing = []
    if phone:
        local = phone.removeprefix("+1")
        if local not in _digits(said):
            missing.append("the phone number, digit by digit")
    if date:
        low = said.lower()
        weekday = _WEEKDAYS[date.weekday()]
        month = _MONTHS[date.month - 1]
        if weekday not in low and not (month in low and re.search(rf"\b{date.day}(?:st|nd|rd|th)?\b", low)):
            missing.append("the date")
    if time:
        h12 = time.hour % 12 or 12
        spoken = f"{h12}{time.minute:02d}" if time.minute else str(h12)
        if spoken not in _digits(said):
            missing.append("the time")
    return f"the read-back didn't include {', '.join(missing)}" if missing else None


_PROMISE = re.compile(
    r"\b(?:call you back|get back to you|(?:take|pass along|pass on|leave) (?:a|your|the) message|"
    r"have (?:them|someone|somebody|a nurse|a doctor|the (?:nurse|doctor|front desk|team|clinic|clinical team|staff)) "
    r"(?:call|contact|reach))", re.IGNORECASE)
# Outcomes that mean something was actually recorded or handed to a person.
_RECORDED = {"message", "booking_requested", "cancel_requested", "transferred", "emergency",
             "system_error"}  # system_error is already flagged for staff


def unkept_follow_up(history: list[tuple[str, str]], outcome: str | None) -> bool:
    """True if the agent talked about a callback or message but nothing was recorded.

    Small models sometimes say "I'll pass that on" without calling take_message. Checked at
    hang-up so staff see the call as needing action instead of the caller waiting for a call
    that never comes. Offers the caller declined will also match; a false alarm costs staff a glance.
    """
    if outcome in _RECORDED:
        return False
    return any(r == "assistant" and t and _PROMISE.search(t) for r, t in history)


def readback_instruction(problem: str) -> str:
    return (f"NOT SAVED — {problem}. Nothing has been recorded, so do not tell the caller it was. Read the details "
            f"back to the caller (name, phone number digit by digit, and any date and time), ask if it's all "
            f"correct, and as soon as they say yes call this tool again.")


# --- tools ------------------------------------------------------------------


class ReceptionistTools:
    """Mixed into the LiveKit Agent. Expects `self.clinic`, `self.backend`, `self.state`, and
    `self.history()` returning [(role, text)], plus `self.transfer()` for SIP transfers."""

    clinic: ClinicConfig
    backend: BackendClient
    state: CallState

    def history(self) -> list[tuple[str, str]]:  # overridden by the agent
        return []

    async def transfer(self, number: str, announcement: str) -> str:  # overridden by the agent
        return "Transfer is not available."

    # Each public tool is a thin wrapper (in core/agent.py) around these `do_*` methods,
    # so they can be tested without LiveKit.

    def _validate(self, tool: str, model: type[BaseModel], args: dict[str, Any]) -> tuple[BaseModel | None, str | None]:
        try:
            return model(**args), None
        except ValidationError as e:
            return None, self._failed(tool, ts.correction_message(e))

    def _failed(self, tool: str, message: str) -> str:
        self.state.failures[tool] += 1
        log.info("tool %s failed (%d): %s", tool, self.state.failures[tool], message)
        if self.state.failures[tool] >= 2 and tool != "take_message":
            return FALLBACK_TO_MESSAGE
        return message

    def _base(self) -> dict[str, Any]:
        return {"call_id": str(self.state.call_id)}

    async def do_check_availability(self, date: str, service: str) -> str:
        p, err = self._validate("check_availability", ts.CheckAvailability, {"date": date, "service": service})
        if err:
            return err
        try:
            r = await self.backend.post("/agent/availability", {**self._base(), **p.model_dump(mode="json")})
        except BackendRejected as e:
            return self._failed("check_availability", f"Invalid request: {e.detail}")
        except BackendError:
            return SYSTEM_DOWN
        self.state.failures["check_availability"] = 0
        day = f"{p.date:%A %B} {p.date.day}"
        if r["unavailable_reason"]:
            return f"No times for {r['service_name']} on {day}: {r['unavailable_reason']}. Suggest another day."
        slots = _spread(r["slots"], 4)
        note = " These are requests the front desk will confirm." if r["requires_confirmation"] else ""
        return (f"Open times for {r['service_name']} (service id {r['service']}) on {day} ({p.date.isoformat()}), "
                f"24-hour: {', '.join(slots)}.{note} Offer two or three of these.")

    async def do_book_appointment(self, name: str, phone: str, date: str, time: str, service: str) -> str:
        p, err = self._validate("book_appointment", ts.BookAppointment,
                                {"name": name, "phone": phone, "date": date, "time": time, "service": service})
        if err:
            return err
        if problem := readback_problem(self.history(), phone=p.phone, date=p.date, time=p.time):
            return readback_instruction(problem)
        try:
            r = await self.backend.post("/agent/appointments", {**self._base(), **p.model_dump(mode="json")})
        except BackendRejected as e:
            return self._failed("book_appointment", f"Invalid request: {e.detail}")
        except BackendError:
            return SYSTEM_DOWN
        if not r["ok"]:
            alts = f" Nearby open times: {', '.join(r['alternatives'])}." if r["alternatives"] else ""
            return f"Could not book: {r['error']}.{alts} Offer the caller another time."
        self.state.failures["book_appointment"] = 0
        self.state.caller_name = self.state.caller_name or p.name
        self.state.outcome = "booking_requested"
        if r["requires_confirmation"]:
            return ("Request recorded. Tell the caller their request is in, and the front desk will call them at "
                    "that number to confirm. Do not say it is confirmed.")
        return "Booked. Tell the caller the appointment is confirmed."

    async def do_cancel_appointment(self, phone: str, date: str) -> str:
        p, err = self._validate("cancel_appointment", ts.CancelAppointment, {"phone": phone, "date": date})
        if err:
            return err
        if problem := readback_problem(self.history(), phone=p.phone, date=p.date):
            return readback_instruction(problem)
        try:
            r = await self.backend.post("/agent/appointments/cancel", {**self._base(), **p.model_dump(mode="json")})
        except BackendRejected as e:
            return self._failed("cancel_appointment", f"Invalid request: {e.detail}")
        except BackendError:
            return SYSTEM_DOWN
        self.state.failures["cancel_appointment"] = 0
        self.state.outcome = self.state.outcome or "cancel_requested"
        return ("Cancellation request recorded. Tell the caller the front desk will process it"
                + (" and call if there's any problem." if r["requires_confirmation"] else "."))

    async def do_take_message(self, name: str, phone: str, reason: str) -> str:
        p, err = self._validate("take_message", ts.TakeMessage, {"name": name, "phone": phone, "reason": reason})
        if err:
            if self.state.failures["take_message"] >= 2:
                # Last resort: keep whatever we have so a person sees it.
                self.state.unsent_messages.append({"name": name, "phone": phone, "reason": reason, "unvalidated": True})
                return "Message noted. Tell the caller the clinic will follow up."
            return err
        history = self.history()
        if not phone_confirmed_earlier(history, p.phone) and (problem := readback_problem(history, phone=p.phone)):
            return readback_instruction(problem)
        payload = {**self._base(), **p.model_dump(mode="json"), "urgency": self.state.urgency}
        self.state.caller_name = self.state.caller_name or p.name
        self.state.reason = self.state.reason or p.reason[:255]
        self.state.outcome = self.state.outcome or "message"
        try:
            await self.backend.post("/agent/messages", payload)
        except (BackendError, BackendRejected):
            self.state.unsent_messages.append(payload)  # sent (or spooled) at hang-up
        return "Message saved. Tell the caller the clinic will call them back."

    async def do_transfer_to_human(self) -> str:
        number = self.clinic.escalation.transfer_number
        announcement = self.clinic.escalation.transfer_announcement
        try:
            r = await self.backend.post("/agent/transfer", {**self._base(), "reason": self.state.reason,
                                                            "urgency": self.state.urgency})
            number, announcement = r["number"], r["announcement"]
        except (BackendError, BackendRejected):
            log.warning("backend unavailable for transfer; using transfer number from clinic config")
        self.state.outcome = "transferred"
        return await self.transfer(number, announcement)


def _spread(slots: list[str], n: int) -> list[str]:
    """Pick n times spread across the day rather than the first n."""
    if len(slots) <= n:
        return slots
    step = (len(slots) - 1) / (n - 1)
    return [slots[round(i * step)] for i in range(n)]


def history_from_items(items: Iterable[Any]) -> list[tuple[str, str]]:
    """Adapt LiveKit ChatContext items to [(role, text)]."""
    out = []
    for item in items:
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)
        if role in ("user", "assistant") and text:
            out.append((role, text))
    return out
