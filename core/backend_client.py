"""The agent's only route to data: HTTP calls to the FastAPI backend.

Timeouts are short because tool calls happen while a caller is waiting. Call
lifecycle writes (start / end / summary) are side-channel: if they fail they
are spooled to disk for replay, so a backend outage never loses a call record.

    python -m core.backend_client replay     # resend spooled writes
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from core.config import REPO_ROOT
from core.settings import Settings, load_settings

log = logging.getLogger("agent.backend")

TOOL_TIMEOUT = httpx.Timeout(3.0, connect=1.5)
SIDE_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
SPOOL_DIR = REPO_ROOT / "var" / "spool"


class BackendError(Exception):
    """The backend could not be reached or returned a server error."""


class BackendRejected(Exception):
    """The backend rejected the request (4xx). `detail` is safe to show the LLM."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class BackendClient:
    def __init__(self, settings: Settings, room: str | None = None, spool_dir: Path = SPOOL_DIR,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        headers = {"Authorization": f"Bearer {settings.agent_api_token.get_secret_value()}"}
        if room:
            headers["X-LiveKit-Room"] = room
        self._http = httpx.AsyncClient(base_url=settings.backend_url.rstrip("/"), headers=headers,
                                       transport=transport)
        self._spool_dir = spool_dir

    async def aclose(self) -> None:
        await self._http.aclose()

    async def post(self, path: str, payload: dict[str, Any], *, timeout: httpx.Timeout = TOOL_TIMEOUT) -> dict:
        try:
            r = await self._http.post(path, json=payload, timeout=timeout)
        except httpx.HTTPError as e:
            raise BackendError(f"{type(e).__name__} calling {path}") from e
        if r.status_code >= 500:
            raise BackendError(f"{r.status_code} from {path}")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text
            raise BackendRejected(r.status_code, _detail_text(detail))
        return r.json()

    async def post_or_spool(self, path: str, payload: dict[str, Any]) -> dict | None:
        """Side-channel write: retry briefly, then spool to disk instead of losing it."""
        for attempt in range(3):
            try:
                return await self.post(path, payload, timeout=SIDE_TIMEOUT)
            except BackendRejected:
                log.exception("backend rejected %s; not retrying", path)
                return None
            except BackendError as e:
                log.warning("backend write %s failed (attempt %d): %s", path, attempt + 1, e)
                await asyncio.sleep(0.5 * (attempt + 1))
        self._spool(path, payload)
        return None

    def _spool(self, path: str, payload: dict[str, Any]) -> None:
        self._spool_dir.mkdir(parents=True, exist_ok=True)
        file = self._spool_dir / f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.json"
        file.write_text(json.dumps({"path": path, "payload": payload}, default=str), encoding="utf-8")
        log.error("backend unreachable; spooled %s to %s", path, file)


def _detail_text(detail: Any) -> str:
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(f"{'.'.join(str(p) for p in d.get('loc', [])[1:])}: {d.get('msg')}" for d in detail)
    return str(detail)


async def replay(settings: Settings, spool_dir: Path = SPOOL_DIR) -> int:
    files = sorted(spool_dir.glob("*.json")) if spool_dir.exists() else []
    client = BackendClient(settings, spool_dir=spool_dir)
    sent = 0
    try:
        for f in files:
            item = json.loads(f.read_text(encoding="utf-8"))
            try:
                await client.post(item["path"], item["payload"], timeout=SIDE_TIMEOUT)
            except BackendError as e:
                print(f"still failing, stopping: {e}")
                break
            except BackendRejected as e:
                print(f"rejected, moving aside: {f.name}: {e}")
                f.rename(f.with_suffix(".rejected"))
                continue
            f.unlink()
            sent += 1
    finally:
        await client.aclose()
    print(f"replayed {sent} of {len(files)}")
    return sent


if __name__ == "__main__":
    if sys.argv[1:] != ["replay"]:
        raise SystemExit(__doc__)
    asyncio.run(replay(load_settings()))
