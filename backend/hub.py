"""In-process WebSocket fan-out to connected dashboards.

One backend process per clinic, so an in-memory set is enough. If the backend is
ever scaled to several processes, swap this for Postgres LISTEN/NOTIFY.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket

log = logging.getLogger("backend.hub")


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    @property
    def size(self) -> int:
        return len(self._clients)

    async def broadcast(self, event: str, data: dict) -> None:
        message = json.dumps({"type": event, "data": data}, default=str)
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await asyncio.wait_for(ws.send_text(message), timeout=2)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.remove(ws)
        if dead:
            log.info("dropped %d stale dashboard connection(s)", len(dead))
