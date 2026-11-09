"""Backend API against in-memory SQLite, with a fixed clock. Fake patient data only."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.main import create_app
from backend.models import AuditLog, Base, StaffRole, StaffUser
from backend.security import hash_password
from core.settings import load_settings

from .conftest import VALID_ENV

# Monday 1 March 2027, 09:00 in Calgary. Defined in local time so the test doesn't
# depend on the UTC offset (which tzdata, not this code, decides).
NOW = dt.datetime(2027, 3, 1, 9, 0, tzinfo=ZoneInfo("America/Edmonton"))
TOKEN = VALID_ENV["AGENT_API_TOKEN"]
AGENT = {"Authorization": f"Bearer {TOKEN}"}
STAFF_EMAIL, STAFF_PASSWORD = "desk@maplegate-clinic.example.com", "correct horse battery"


@pytest.fixture
def client(clean_env, clinics_root):
    for k, v in {**VALID_ENV, "DATABASE_URL": "sqlite+aiosqlite:///:memory:"}.items():
        clean_env.setenv(k, v)
    app = create_app(load_settings(clinics_root=clinics_root), clock=lambda: NOW)
    with TestClient(app) as c:
        async def setup():
            async with app.state.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with app.state.sessionmaker() as s:
                s.add(StaffUser(email=STAFF_EMAIL, name="Front Desk", password_hash=hash_password(STAFF_PASSWORD),
                                role=StaffRole.admin))
                await s.commit()
        c.portal.call(setup)
        c.app_ = app
        yield c


def login(client) -> None:
    r = client.post("/auth/login", json={"email": STAFF_EMAIL.upper(), "password": STAFF_PASSWORD})
    assert r.status_code == 200, r.text


def audit_rows(client) -> list[AuditLog]:
    async def q():
        async with client.app_.state.sessionmaker() as s:
            return list(await s.scalars(select(AuditLog).order_by(AuditLog.id)))
    return client.portal.call(q)


def start_call(client, phone=None) -> str:
    r = client.post("/agent/calls", json={"room_name": "room-1", "caller_phone": phone}, headers=AGENT)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- auth -------------------------------------------------------------------


def test_agent_endpoints_need_token(client):
    assert client.post("/agent/calls", json={"room_name": "x"}).status_code == 401
    assert client.post("/agent/calls", json={"room_name": "x"},
                       headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_staff_endpoints_need_login(client):
    assert client.get("/calls").status_code == 401
    assert client.get("/auth/me").status_code == 401


def test_login_wrong_password_is_audited(client):
    r = client.post("/auth/login", json={"email": STAFF_EMAIL, "password": "nope"})
    assert r.status_code == 401
    assert client.post("/auth/login", json={"email": "nobody@example.com", "password": "nope"}).status_code == 401
    assert [a.action for a in audit_rows(client)] == ["login_failed", "login_failed"]


def test_login_sets_httponly_cookie(client):
    r = client.post("/auth/login", json={"email": STAFF_EMAIL, "password": STAFF_PASSWORD})
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/auth/me").json()["email"] == STAFF_EMAIL


# --- availability -----------------------------------------------------------


def avail(client, date, service="standard_visit"):
    return client.post("/agent/availability", json={"date": date, "service": service}, headers=AGENT)


def test_availability_respects_hours_and_notice(client):
    r = avail(client, "2027-03-01").json()
    assert r["unavailable_reason"] is None
    assert r["requires_confirmation"] is True
    assert r["slots"][0] == "11:00"            # 09:00 now + 2h minimum notice
    assert r["slots"][-1] == "16:45"           # last 15-min slot before 17:00 close


def test_availability_service_by_name(client):
    r = avail(client, "2027-03-02", "extended appointment").json()
    assert r["service"] == "extended_visit"
    assert r["slots"][-1] == "16:30"           # 30 min visit must end by 17:00


@pytest.mark.parametrize(("date", "service", "reason"), [
    ("2027-03-07", "standard_visit", "closed on Sunday"),
    ("2027-03-26", "standard_visit", "closed on Friday, March 26"),   # Good Friday
    ("2027-02-27", "standard_visit", "in the past"),
    ("2027-06-30", "standard_visit", "60 days ahead"),
    ("2027-03-02", "walk_in", "can't be booked by phone"),
])
def test_availability_unavailable(client, date, service, reason):
    r = avail(client, date, service).json()
    assert r["slots"] == []
    assert reason in r["unavailable_reason"]


def test_unknown_service_is_422_with_options(client):
    r = avail(client, "2027-03-02", "massage")
    assert r.status_code == 422
    assert "Family doctor appointment" in r.json()["detail"]


# --- booking flow -----------------------------------------------------------


BOOKING = {"name": "Jordan Lee", "phone": "(403) 555-0111", "date": "2027-03-02", "time": "10:30",
           "service": "standard_visit"}


def test_full_booking_call_flow(client):
    call_id = start_call(client)
    r = client.post("/agent/appointments", json={**BOOKING, "call_id": call_id}, headers=AGENT).json()
    assert r["ok"] and r["requires_confirmation"]

    # same slot is no longer offered
    assert "10:30" not in avail(client, "2027-03-02").json()["slots"]

    r = client.post(f"/agent/calls/{call_id}/end", headers=AGENT, json={
        "transcript": [{"role": "user", "text": "I'd like to book"}],
        "latency": [{"turn": 1, "eou": 0.3, "llm_ttft": 0.4, "tts_ttfb": 0.2, "total": 0.9}],
    })
    assert r.status_code == 200

    login(client)
    calls = client.get("/calls").json()["items"]
    assert len(calls) == 1
    c = calls[0]
    assert c["caller_name"] == "Jordan Lee"
    assert c["caller_phone"] == "+14035550111"
    assert c["outcome"] == "booking_requested"
    assert c["appointment_requests"][0]["booking_status"] == "requested"

    notices = client.app_.state.notifier.sent
    assert any("Appointment request: Jordan Lee" in n.subject for n in notices)


def test_booking_taken_slot_offers_alternatives(client):
    client.post("/agent/appointments", json=BOOKING, headers=AGENT)
    r = client.post("/agent/appointments", json={**BOOKING, "name": "Riley Park", "phone": "4035550122"},
                    headers=AGENT).json()
    assert not r["ok"]
    assert r["alternatives"] == ["10:00", "10:15", "10:45"]  # three nearest open times, in order


@pytest.mark.parametrize("bad", [{"phone": "12345"}, {"name": "4035550111"}, {"time": "25:00"}, {"date": "tomorrow"}])
def test_booking_rejects_bad_payload(client, bad):
    r = client.post("/agent/appointments", json={**BOOKING, **bad}, headers=AGENT)
    assert r.status_code == 422


def test_cancel_matches_existing_request(client):
    client.post("/agent/appointments", json=BOOKING, headers=AGENT)
    r = client.post("/agent/appointments/cancel", json={"phone": "403 555 0111", "date": "2027-03-02"},
                    headers=AGENT).json()
    assert r == {"ok": True, "matched": 1, "requires_confirmation": True, "error": None}
    assert "10:30" in avail(client, "2027-03-02").json()["slots"]  # slot freed


def test_cancel_without_match_records_request_for_staff(client):
    r = client.post("/agent/appointments/cancel", json={"phone": "4035550199", "date": "2027-03-03"},
                    headers=AGENT).json()
    assert r["ok"] and r["matched"] == 0
    login(client)
    reqs = client.get("/appointments").json()
    assert reqs[0]["kind"] == "cancel" and reqs[0]["booking_status"] == "cancel_requested"


# --- messages, transfer, urgency -------------------------------------------


def test_message_is_stored_and_staff_notified(client):
    call_id = start_call(client)
    r = client.post("/agent/messages", headers=AGENT, json={
        "call_id": call_id, "name": "Priya Natarajan", "phone": "587-555-0123", "reason": "Refill for my inhaler"})
    assert r.status_code == 201
    login(client)
    msgs = client.get("/messages").json()
    assert msgs[0]["caller_phone"] == "+15875550123"
    assert client.app_.state.notifier.sent[-1].subject == "Message from Priya Natarajan"


def test_transfer_returns_clinic_number(client):
    call_id = start_call(client)
    r = client.post("/agent/transfer", json={"call_id": call_id, "reason": "medication question",
                                              "urgency": "urgent"}, headers=AGENT).json()
    assert r["number"] == "+14035550199"


def test_emergency_call_alerts_staff_and_urgency_never_downgrades(client):
    call_id = start_call(client)
    client.post("/agent/transfer", json={"call_id": call_id, "urgency": "emergency"}, headers=AGENT)
    client.post(f"/agent/calls/{call_id}/end", json={"urgency": "routine", "outcome": "answered"}, headers=AGENT)
    login(client)
    c = client.get(f"/calls/{call_id}").json()
    assert c["urgency"] == "emergency"
    assert c["outcome"] == "transferred"
    assert client.app_.state.notifier.sent[-1].urgent


# --- staff actions + audit --------------------------------------------------


def test_staff_update_and_audit_trail(client):
    call_id = start_call(client)
    client.post(f"/agent/calls/{call_id}/end", json={}, headers=AGENT)
    login(client)
    me = client.get("/auth/me").json()

    r = client.patch(f"/calls/{call_id}", json={"status": "Handled", "notes": "Called back", "assigned_to_id": me["id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "Handled" and body["notes"] == "Called back" and body["assigned_to"]["id"] == me["id"]

    client.get(f"/calls/{call_id}")
    rows = [(a.actor_type, a.action, a.entity) for a in audit_rows(client)]
    assert ("staff", "update", "call") in rows
    assert ("staff", "read", "call") in rows
    update = next(a for a in audit_rows(client) if a.action == "update" and a.actor_type == "staff")
    assert update.details["status"] == ["New", "Handled"]
    assert update.details["notes"] == "changed"  # note text is not copied into the audit log


def test_filters(client):
    a = start_call(client)
    b = start_call(client)
    client.post("/agent/transfer", json={"call_id": b, "urgency": "urgent"}, headers=AGENT)
    login(client)
    client.patch(f"/calls/{a}", json={"status": "Handled"})
    assert [c["id"] for c in client.get("/calls?urgent=true").json()["items"]] == [b]
    assert [c["id"] for c in client.get("/calls?status=New").json()["items"]] == [b]
    assert len(client.get("/calls?today=true").json()["items"]) == 2


def test_websocket_pushes_call_events(client):
    login(client)
    cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    with client.websocket_connect("/ws", headers={"cookie": cookie, "origin": "http://localhost:3000"}) as ws:
        call_id = start_call(client)
        assert ws.receive_json()["type"] == "call.started"
        client.post(f"/agent/calls/{call_id}/end", json={"caller_name": "Jordan Lee"}, headers=AGENT)
        event = ws.receive_json()
        assert event["type"] == "call.ended" and event["data"]["caller_name"] == "Jordan Lee"


def test_websocket_rejects_anonymous_and_foreign_origin(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as ws:
            ws.receive_text()
    login(client)
    cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"cookie": cookie, "origin": "https://evil.example"}) as ws:
            ws.receive_text()


def test_flag_alerts_staff_immediately(client):
    call_id = start_call(client, phone="+18255550134")
    r = client.post(f"/agent/calls/{call_id}/flag", json={"urgency": "emergency", "reason": "chest pain"},
                    headers=AGENT)
    assert r.status_code == 200
    notice = client.app_.state.notifier.sent[-1]
    assert notice.urgent and "9-1-1" in notice.body and "(825) 555-0134" in notice.body
    # flagging again at the same level doesn't re-alert
    client.post(f"/agent/calls/{call_id}/flag", json={"urgency": "emergency"}, headers=AGENT)
    assert len(client.app_.state.notifier.sent) == 1


def test_start_call_is_idempotent_with_client_id(client):
    cid = "6f1c1a52-3a8e-4c3f-9a51-1d4f7b2c9e10"
    for _ in range(2):
        r = client.post("/agent/calls", json={"id": cid, "room_name": "r"}, headers=AGENT)
        assert r.json()["id"] == cid
    login(client)
    assert len(client.get("/calls").json()["items"]) == 1


def test_timestamps_are_returned_with_utc_offset(client):
    # SQLite drops tz info; the API must still say UTC or the dashboard shows the wrong time.
    start_call(client)
    login(client)
    started = client.get("/calls").json()["items"][0]["started_at"]
    assert started.endswith(("Z", "+00:00"))
