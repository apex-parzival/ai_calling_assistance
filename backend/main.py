"""FastAPI backend: tool endpoints for the agent, staff dashboard API, logging.

Run locally:  uvicorn backend.main:create_app --factory --reload
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import db
from backend.hub import Hub
from backend.notify import Notifier
from backend.routes import agent, appointments, auth, calls, health, messages, ws
from core.config import load_clinic_config
from core.settings import Settings, load_settings, problems, require

log = logging.getLogger("backend")


def create_app(settings: Settings | None = None, clock: Callable[[], datetime] | None = None) -> FastAPI:
    # Validate everything before the app object exists, so a bad config or a
    # missing key stops the process at startup rather than on the first request.
    settings = settings or load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    require(settings, ["backend", "notify"])
    clinic = load_clinic_config(settings.clinic_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = db.make_engine(settings.database_url)
        app.state.sessionmaker = db.make_sessionmaker(app.state.engine)
        log.info("clinic %s (%s) loaded; environment=%s", clinic.clinic_id, clinic.name, settings.environment)
        for p in problems(settings, ["agent", "telephony"], tts_provider=clinic.voice.tts_provider):
            log.warning("not set up yet: %s", p)
        if not await db.ping(app.state.engine):
            log.warning("database not reachable yet at startup; /health will report 503 until it is")
        yield
        await app.state.notifier.drain()
        await app.state.engine.dispose()

    app = FastAPI(title=f"{clinic.name} receptionist backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.clinic = clinic
    app.state.hub = Hub()
    app.state.notifier = Notifier(settings, clinic)
    app.state.clock = clock or (lambda: datetime.now(timezone.utc))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.dashboard_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type"],
    )
    for module in (health, auth, calls, appointments, messages, agent, ws):
        app.include_router(module.router)
    return app
