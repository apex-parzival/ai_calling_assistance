"""The five tools, end to end: tool -> BackendClient -> real FastAPI app -> SQLite.
No LiveKit, no vendor keys. Fake patient data only."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from backend.main import create_app
from backend.models import AppointmentRequest, Base, Call, Message
from core.backend_client import BackendClient
from core.config import load_clinic_config
from core.settings import load_settings
from core.tools import FALLBACK_TO_MESSAGE, SYSTEM_DOWN, CallState, ReceptionistTools, readback_problem

from .conftest import VALID_ENV

NOW = dt.datetime(2027, 3, 1, 9, 0, tzinfo=ZoneInfo("America/Edmonton"))


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeAgent(ReceptionistTools):
    def __init__(self, clinic, backend, state):
        self.clinic, self.backend, self.state = clinic, backend, state
        self.turns: list[tuple[str, str]] = []
        self.transfers: list[str] = []

    def history(self):
        return self.turns

    async def transfer(self, number, announcement):
        self.transfers.append(number)
        return f"transferred to {number}"


@pytest.fixture
async def env(clean_env, clinics_root, tmp_path):
    for k, v in {**VALID_ENV, "DATABASE_URL": "sqlite+aiosqlite:///:memory:"}.items():
        clean_env.setenv(k, v)
    settings = load_settings(clinics_root=clinics_root)
    app = create_app(settings, clock=lambda: NOW)
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        backend = BackendClient(settings, spool_dir=tmp_path / "spool", transport=httpx.ASGITransport(app=app))
        state = CallState()
        await backend.post("/agent/calls", {"id": str(state.call_id), "room_name": "test"})
        agent = FakeAgent(load_clinic_config(settings.clinic_dir), backend, state)
        agent.app = app
        yield agent
        await backend.aclose()


async def rows(agent, model):
    async with agent.app.state.sessionmaker() as s:
        return list(await s.scalars(select(model)))


def confirm(agent, readback: str, answer: str = "Yes, that's right.") -> None:
    agent.turns += [("assistant", readback), ("user", answer)]


# --- check_availability -----------------------------------------------------


@pytest.mark.anyio
async def test_availability_offers_spread_of_times(env):
    out = await env.do_check_availability("2027-03-02", "standard_visit")
    assert "Family doctor appointment" in out and "2027-03-02" in out
    assert "08:00" in out and "16:45" in out  # first and last of the day
    assert "front desk will confirm" in out


@pytest.mark.anyio
async def test_availability_bad_date_gets_correction_then_fallback(env):
    first = await env.do_check_availability("next tuesday", "standard_visit")
    assert "invalid" in first and "date" in first
    second = await env.do_check_availability("tuesday-ish", "standard_visit")
    assert second == FALLBACK_TO_MESSAGE


@pytest.mark.anyio
async def test_availability_closed_day_suggests_another(env):
    out = await env.do_check_availability("2027-03-07", "standard_visit")
    assert "closed on Sunday" in out and "another day" in out


# --- book_appointment -------------------------------------------------------


@pytest.mark.anyio
async def test_booking_refused_without_readback(env):
    env.turns = [("user", "Book me Tuesday at 10:30, Jordan Lee, 403 555 0111")]
    out = await env.do_book_appointment("Jordan Lee", "403-555-0111", "2027-03-02", "10:30", "standard_visit")
    assert out.startswith("NOT SAVED") and "do not tell the caller it was" in out
    assert await rows(env, AppointmentRequest) == []


@pytest.mark.anyio
async def test_booking_refused_when_caller_corrects(env):
    confirm(env, "That's Jordan Lee, 4 0 3, 5 5 5, 0 1 1 1, Tuesday March 2nd at 10:30 a.m. Correct?",
            "No, it's 0 1 1 2 at the end")
    out = await env.do_book_appointment("Jordan Lee", "4035550111", "2027-03-02", "10:30", "standard_visit")
    assert "wasn't a clear yes" in out


@pytest.mark.anyio
async def test_booking_refused_when_readback_has_wrong_number(env):
    confirm(env, "That's Jordan Lee at 4 0 3, 5 5 5, 0 1 1 2, Tuesday March 2nd at 10:30 a.m.?")
    out = await env.do_book_appointment("Jordan Lee", "4035550111", "2027-03-02", "10:30", "standard_visit")
    assert "phone number" in out


@pytest.mark.anyio
async def test_booking_after_readback_records_request(env):
    confirm(env, "So that's Jordan Lee, four oh three, five five five, zero one one one, for a family doctor "
                 "appointment on Tuesday, March 2nd at 10:30 a.m. Is that all correct?")
    out = await env.do_book_appointment("Jordan Lee", "(403) 555-0111", "2027-03-02", "10:30", "standard_visit")
    assert "Request recorded" in out and "Do not say it is confirmed" in out
    [req] = await rows(env, AppointmentRequest)
    assert req.patient_phone == "+14035550111" and req.call_id == env.state.call_id
    assert env.state.outcome == "booking_requested"


@pytest.mark.anyio
async def test_booking_afternoon_time_readback_in_12h(env):
    confirm(env, "Jordan Lee, 403-555-0111, Tuesday March 2nd at 2:15 p.m. Right?", "yep")
    out = await env.do_book_appointment("Jordan Lee", "4035550111", "2027-03-02", "14:15", "standard_visit")
    assert "Request recorded" in out


@pytest.mark.anyio
async def test_booking_taken_time_returns_alternatives(env):
    confirm(env, "Jordan Lee, 403 555 0111, Tuesday at 10:30 a.m.?")
    await env.do_book_appointment("Jordan Lee", "4035550111", "2027-03-02", "10:30", "standard_visit")
    confirm(env, "Riley Park, 403 555 0122, Tuesday at 10:30 a.m.?")
    out = await env.do_book_appointment("Riley Park", "4035550122", "2027-03-02", "10:30", "standard_visit")
    assert "Could not book" in out and "10:15" in out


@pytest.mark.anyio
async def test_unknown_service_is_corrected(env):
    confirm(env, "Jordan Lee, 403 555 0111, Tuesday at 10:30 a.m.?")
    out = await env.do_book_appointment("Jordan Lee", "4035550111", "2027-03-02", "10:30", "massage")
    assert "Invalid request" in out and "Family doctor appointment" in out


# --- cancel / message / transfer -------------------------------------------


@pytest.mark.anyio
async def test_cancel_after_readback(env):
    confirm(env, "To confirm, cancel the appointment for 403 555 0111 on Wednesday, March 3rd?")
    out = await env.do_cancel_appointment("4035550111", "2027-03-03")
    assert "Cancellation request recorded" in out
    [req] = await rows(env, AppointmentRequest)
    assert req.kind.value == "cancel"


@pytest.mark.anyio
async def test_take_message_after_readback(env):
    confirm(env, "I have Priya Natarajan at 5 8 7, 5 5 5, 0 1 2 3. Is that right?")
    out = await env.do_take_message("Priya Natarajan", "5875550123", "Asking for an inhaler refill")
    assert "Message saved" in out
    [msg] = await rows(env, Message)
    assert msg.caller_phone == "+15875550123"


@pytest.mark.anyio
async def test_take_message_when_number_was_confirmed_before_the_reason(env):
    # Seen with the real LLM: number confirmed, then the reason asked, then take_message called.
    confirm(env, "Just to confirm, your phone number is 587-555-0123?", "Yes, that's correct.")
    env.turns += [("assistant", "And what's the message for the nurse?"),
                  ("user", "Just asking her to call me back about my lab requisition.")]
    out = await env.do_take_message("Priya Natarajan", "5875550123", "Call back about lab requisition")
    assert "Message saved" in out
    assert len(await rows(env, Message)) == 1


@pytest.mark.parametrize("said, outcome, flagged", [
    # Seen with the real LLM: promised, never called take_message.
    ("I'll take a message for the clinical team and have them call you back at 403-555-0111.", None, True),
    ("I'll pass that on to the front desk and have the nurse call you.", "answered", True),
    ("I'll take a message and have them call you back.", "message", False),       # was recorded
    ("Your request is in; the front desk will call you to confirm.", "booking_requested", False),
    ("We're open 8 to 6 on weekdays. Anything else?", "answered", False),
])
def test_unkept_follow_up(said, outcome, flagged):
    from core.tools import unkept_follow_up
    history = [("user", "Can the nurse call me?"), ("assistant", said), ("user", "Thanks, bye.")]
    assert unkept_follow_up(history, outcome) is flagged


@pytest.mark.anyio
async def test_take_message_refused_when_confirmed_number_was_different(env):
    confirm(env, "Your number is 587-555-0123?", "Yes.")
    env.turns += [("assistant", "And the reason?"), ("user", "Oh wait, use my cell, 587-555-0199. Lab results.")]
    out = await env.do_take_message("Priya Natarajan", "5875550199", "Lab results")
    assert out.startswith("NOT SAVED")
    assert await rows(env, Message) == []


@pytest.mark.anyio
async def test_take_message_survives_backend_outage(env, tmp_path, clean_env, clinics_root):
    settings = load_settings(clinics_root=clinics_root)
    env.backend = BackendClient(settings, spool_dir=tmp_path / "s2",
                                transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    confirm(env, "Priya Natarajan at 587 555 0123, right?")
    out = await env.do_take_message("Priya Natarajan", "5875550123", "Asking for an inhaler refill")
    assert "Message saved" in out
    assert env.state.unsent_messages and env.state.unsent_messages[0]["phone"] == "+15875550123"
    assert await env.do_check_availability("2027-03-02", "standard_visit") == SYSTEM_DOWN


@pytest.mark.anyio
async def test_spool_when_backend_down(env, tmp_path, clean_env, clinics_root):
    settings = load_settings(clinics_root=clinics_root)
    spool = tmp_path / "s3"
    client = BackendClient(settings, spool_dir=spool, transport=httpx.MockTransport(lambda r: httpx.Response(502)))
    assert await client.post_or_spool("/agent/calls/x/end", {"a": 1}) is None
    assert len(list(spool.glob("*.json"))) == 1


@pytest.mark.anyio
async def test_transfer_records_and_uses_clinic_number(env):
    env.state.reason = "medication question"
    out = await env.do_transfer_to_human()
    assert out == "transferred to +14035550199"
    [call] = await rows(env, Call)
    assert call.outcome == "transferred"


# --- read-back helper -------------------------------------------------------


def test_readback_requires_user_last():
    h = [("user", "yes"), ("assistant", "403 555 0111?")]
    assert readback_problem(h, phone="+14035550111") == "the caller hasn't confirmed yet"


def test_readback_ok_with_spelled_digits_and_date_by_month():
    h = [("assistant", "four zero three five five five zero one one one, on March 2nd at 9 a.m.?"), ("user", "correct")]
    assert readback_problem(h, phone="+14035550111", date=dt.date(2027, 3, 2), time=dt.time(9, 0)) is None


def test_readback_missing_date():
    h = [("assistant", "403 555 0111 at 9 a.m.?"), ("user", "yes")]
    assert "the date" in readback_problem(h, phone="+14035550111", date=dt.date(2027, 3, 2), time=dt.time(9, 0))
