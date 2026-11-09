"""Simulate a full booking call against a running backend — no LiveKit, no vendor keys —
and measure how long the finished call takes to reach a connected dashboard.

    python -m tests.scenarios.simulate_call --email frontdesk@... --password ...

It plays the agent's side over the real agent API (start -> availability -> book ->
end -> summary) while a WebSocket client logged in as staff watches for the events.
Fake patient data only.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import time
import uuid

import httpx
import websockets

from core.config import load_clinic_config
from core.settings import load_settings


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--origin", default="http://localhost:3000")
    args = ap.parse_args()

    settings = load_settings()
    clinic = load_clinic_config(settings.clinic_dir)
    base = settings.backend_url.rstrip("/")
    agent = {"Authorization": f"Bearer {settings.agent_api_token.get_secret_value()}"}

    async with httpx.AsyncClient(base_url=base, timeout=10) as http:
        r = await http.post("/auth/login", json={"email": args.email, "password": args.password})
        r.raise_for_status()
        cookie = "; ".join(f"{k}={v}" for k, v in http.cookies.items())

        ws_url = base.replace("http", "ws", 1) + "/ws"
        async with websockets.connect(ws_url, additional_headers={"Cookie": cookie, "Origin": args.origin}) as ws:
            events: list[tuple[float, dict]] = []

            async def listen():
                async for raw in ws:
                    events.append((time.perf_counter(), json.loads(raw)))

            listener = asyncio.create_task(listen())
            call_id = str(uuid.uuid4())
            (await http.post("/agent/calls", headers=agent, json={"id": call_id, "room_name": "simulated"})).raise_for_status()

            # Find the next open weekday and book its first slot.
            day = dt.date.today()
            while True:
                day += dt.timedelta(days=1)
                avail = (await http.post("/agent/availability", headers=agent,
                                         json={"date": day.isoformat(), "service": clinic.services[0].id})).json()
                if avail["slots"]:
                    break
            book = (await http.post("/agent/appointments", headers=agent, json={
                "call_id": call_id, "name": "Jordan Lee", "phone": "403-555-0111", "date": day.isoformat(),
                "time": avail["slots"][0], "service": clinic.services[0].id})).json()
            assert book["ok"], book

            t_end = time.perf_counter()
            (await http.post(f"/agent/calls/{call_id}/end", headers=agent, json={
                "caller_name": "Jordan Lee", "outcome": "booking_requested",
                "transcript": [{"role": "assistant", "text": clinic.greeting},
                               {"role": "user", "text": "[simulated] I'd like to book an appointment."}],
                "latency": [{"turn": 1, "total": 0.9}],
            })).raise_for_status()
            await asyncio.sleep(0.5)
            listener.cancel()

    ended = [t for t, e in events if e["type"] == "call.ended" and e["data"]["id"] == call_id]
    if not ended:
        print("FAIL: dashboard never received call.ended")
        return 1
    ms = (ended[0] - t_end) * 1000
    print(f"booked {day} {avail['slots'][0]}; call.ended reached the dashboard {ms:.0f} ms after hang-up")
    print("PASS" if ms < 1000 else "FAIL: over 1 second")
    return 0 if ms < 1000 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
