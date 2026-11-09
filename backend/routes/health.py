from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request, Response

from backend import db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request, response: Response) -> dict:
    """200 when the backend is up and Postgres answers; 503 otherwise."""
    state = request.app.state
    try:
        db_ok = await asyncio.wait_for(db.ping(state.engine), timeout=3)
    except TimeoutError:
        db_ok = False
    if not db_ok:
        response.status_code = 503
    return {
        "status": "ok" if db_ok else "degraded",
        "clinic": state.clinic.clinic_id,
        "database": "ok" if db_ok else "unreachable",
        "environment": state.settings.environment,
    }
