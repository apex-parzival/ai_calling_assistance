"""Admin commands.

    python -m backend.cli migrate                        # alembic upgrade head
    python -m backend.cli create-staff --email a@b.ca --name "Front Desk" [--admin]
    python -m backend.cli seed-demo                      # FAKE calls for dashboard development
    python -m backend.cli latency-report [--days 7] [--write]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import getpass
import os
import random
import sys

from sqlalchemy import select

from backend.db import make_engine, make_sessionmaker
from backend.models import (
    AppointmentRequest, BookingStatus, Call, CallStatus, Message, StaffRole, StaffUser, Urgency, utcnow,
)
from backend.security import hash_password
from core.config import REPO_ROOT
from core.settings import load_settings, require


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")


async def create_staff(email: str, name: str, admin: bool, password: str | None) -> None:
    password = password or os.environ.get("STAFF_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    if len(password) < 12:
        raise SystemExit("password must be at least 12 characters")
    s = load_settings()
    require(s, ["backend"])
    engine = make_engine(s.database_url)
    async with make_sessionmaker(engine)() as session:
        if await session.scalar(select(StaffUser).where(StaffUser.email == email.lower())):
            raise SystemExit(f"{email} already exists")
        session.add(StaffUser(email=email.lower(), name=name, password_hash=hash_password(password),
                              role=StaffRole.admin if admin else StaffRole.staff))
        await session.commit()
    await engine.dispose()
    print(f"created {'admin' if admin else 'staff'} user {email}")


# Fictional people only. Never put real patient data in a dev database.
_FAKE = [
    ("Jordan Lee", "+14035550111", "Book a family doctor appointment", Urgency.routine, "booking_requested"),
    ("Priya Natarajan", "+15875550123", "Prescription refill request", Urgency.routine, "message"),
    ("Sam Okafor", "+18255550134", "Caller reported chest pain; told to call 911", Urgency.emergency, "emergency"),
    ("Taylor Fournier", "+14035550145", "Asked about parking and hours", Urgency.routine, "answered"),
    ("Morgan Chen", "+15875550156", "Worried about medication side effects", Urgency.urgent, "transferred"),
]


async def seed_demo() -> None:
    s = load_settings()
    require(s, ["backend"])
    if s.environment == "production":
        raise SystemExit("refusing to seed demo data in production")
    engine = make_engine(s.database_url)
    now = utcnow()
    async with make_sessionmaker(engine)() as session:
        for i, (name, phone, reason, urgency, outcome) in enumerate(_FAKE):
            started = now - dt.timedelta(hours=i * 3, minutes=random.randint(0, 50))
            call = Call(
                room_name=f"demo-{i}", caller_name=name, caller_phone=phone, started_at=started,
                ended_at=started + dt.timedelta(seconds=95 + i * 20), duration=95 + i * 20, reason=reason,
                urgency=urgency, outcome=outcome, status=CallStatus.new if i < 3 else CallStatus.handled,
                summary=f"[demo] {reason}.",
                transcript=[
                    {"role": "assistant", "text": "Thank you for calling. I'm the clinic's AI assistant…"},
                    {"role": "user", "text": f"[demo transcript] {reason}"},
                ],
                latency=[{"turn": 1, "eou": 0.35, "llm_ttft": 0.42, "tts_ttfb": 0.21, "total": 0.98}],
            )
            session.add(call)
            await session.flush()
            if outcome == "booking_requested":
                session.add(AppointmentRequest(
                    call_id=call.id, patient_name=name, patient_phone=phone,
                    requested_date=(now + dt.timedelta(days=2)).date(), requested_time=dt.time(10, 30),
                    service="standard_visit", booking_status=BookingStatus.requested))
            if outcome == "message":
                session.add(Message(call_id=call.id, caller_name=name, caller_phone=phone, reason=reason))
        await session.commit()
    await engine.dispose()
    print(f"seeded {len(_FAKE)} fake calls")


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, round(p * (len(s) - 1)))]


async def latency_report(days: int, write: bool) -> None:
    """Median / p90 reply latency per leg over recent calls (Phase 1 exit: median under 1.5 s)."""
    s = load_settings()
    require(s, ["backend"])
    engine = make_engine(s.database_url)
    since = utcnow() - dt.timedelta(days=days)
    async with make_sessionmaker(engine)() as session:
        calls = list(await session.scalars(select(Call).where(Call.started_at >= since, Call.latency.is_not(None))))
    # Seeded and simulated calls carry made-up timings; only real agent calls count.
    calls = [c for c in calls if not (c.room_name or "").startswith(("demo-", "simulated"))]
    await engine.dispose()
    legs = {"total": [], "eou": [], "stt": [], "llm_ttft": [], "tts_ttfb": []}
    for call in calls:
        for sample in call.latency or []:
            for leg, values in legs.items():
                if isinstance(sample.get(leg), (int, float)):
                    values.append(float(sample[leg]))
    n = len(legs["total"])
    fmt = lambda v: f"{v:.2f}s" if v is not None else "-"  # noqa: E731
    lines = [
        f"## Latency - {dt.date.today().isoformat()} (last {days} days, {len(calls)} calls, {n} replies)",
        "",
        "| Leg | Median | p90 |",
        "| --- | --- | --- |",
        *(f"| {leg} | {fmt(_pct(v, 0.5))} | {fmt(_pct(v, 0.9))} |" for leg, v in legs.items()),
        "",
        f"Target: median total under 1.5 s: "
        f"{'PASS' if n and _pct(legs['total'], 0.5) < 1.5 else 'FAIL' if n else 'no data yet'}",
    ]
    report = "\n".join(lines)
    print(report)
    if write:
        out = REPO_ROOT / "docs" / "latency-log.md"
        out.parent.mkdir(exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            f.write(report + "\n\n")
        print(f"\nappended to {out}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    cs = sub.add_parser("create-staff")
    cs.add_argument("--email", required=True)
    cs.add_argument("--name", required=True)
    cs.add_argument("--admin", action="store_true")
    cs.add_argument("--password", help="omit to be prompted (or set STAFF_PASSWORD)")
    sub.add_parser("seed-demo")
    lr = sub.add_parser("latency-report")
    lr.add_argument("--days", type=int, default=7)
    lr.add_argument("--write", action="store_true", help="append the report to docs/latency-log.md")
    args = p.parse_args(argv)

    if args.cmd == "migrate":
        migrate()
    elif args.cmd == "create-staff":
        asyncio.run(create_staff(args.email, args.name, args.admin, args.password))
    elif args.cmd == "seed-demo":
        asyncio.run(seed_demo())
    elif args.cmd == "latency-report":
        asyncio.run(latency_report(args.days, args.write))
    return 0


if __name__ == "__main__":
    sys.exit(main())
