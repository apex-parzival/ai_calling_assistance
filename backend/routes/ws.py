"""Live dashboard updates. Events: call.started, call.ended, call.updated."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from backend.security import staff_for_websocket

router = APIRouter()


@router.websocket("/ws")
async def dashboard_ws(ws: WebSocket):
    origin = ws.headers.get("origin")
    if origin and origin != ws.app.state.settings.dashboard_origin:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    user = await staff_for_websocket(ws)
    if user is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()
    hub = ws.app.state.hub
    await hub.add(ws)
    try:
        while True:
            # Clients only send keepalive pings; content is ignored.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(ws)
