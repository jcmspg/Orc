"""Local server the desktop app talks to: one WebSocket, events out, ops in."""

from __future__ import annotations

import asyncio
import hmac
import os
import signal
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from orc import credentials
from orc.engine import Emit, Engine
from orc.runtimes import runtime_for
from orc.sandbox import DockerSandbox

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Hub:
    """Fans every event out to each connected client's queue, in order."""

    def __init__(self) -> None:
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()

    def broadcast(self, event: dict[str, Any]) -> None:
        for q in self.clients:
            q.put_nowait(event)


def default_engine(emit: Emit) -> Engine:
    return Engine(emit, sandbox_factory=DockerSandbox.create, runtime_for=runtime_for)


def origin_allowed(origin: str | None) -> bool:
    """Block web pages: browsers let any site open a WebSocket to localhost.

    Allowed: no Origin (non-browser clients), the Electron app (`file://`, `null`)
    and local dev servers.
    """
    if origin in (None, "", "null", "file://"):
        return True
    parsed = urlparse(origin)
    return parsed.scheme in ("http", "https") and parsed.hostname in LOCAL_HOSTS


async def watch_parent(interval: float = 2.0) -> None:
    """If the desktop app that started us dies without stopping us, stop ourselves cleanly.

    SIGTERM to our own process runs uvicorn's normal shutdown, which destroys the black boxes.
    """
    pid = int(os.environ.get("ORC_PARENT_PID") or 0)
    if not pid:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            os.kill(os.getpid(), signal.SIGTERM)
            return
        except PermissionError:
            pass  # exists, owned by someone else: still alive


def credentials_event(error: str | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {"type": "app.credentials", "status": credentials.status()}
    if error:
        event["error"] = error
    return event


def create_app(
    engine_factory: Callable[[Emit], Engine] = default_engine,
    session_token: str | None = None,
) -> FastAPI:
    hub = Hub()
    state: dict[str, Engine] = {}
    # Set by the desktop app per launch; when present, clients must present it.
    token = session_token if session_token is not None else os.environ.get("ORC_SESSION_TOKEN") or None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        credentials.load_into_environ()
        engine = engine_factory(hub.broadcast)
        engine.boot()
        state["engine"] = engine
        watchdog = asyncio.create_task(watch_parent())
        yield
        watchdog.cancel()
        await engine.shutdown()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        presented = sock.query_params.get("token") or ""
        if not origin_allowed(sock.headers.get("origin")) or (token and not hmac.compare_digest(presented, token)):
            await sock.close(code=1008)
            return
        await sock.accept()
        engine = state["engine"]
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Snapshot and subscribe with no await in between, so no event is missed.
        for event in engine.board.snapshot():
            queue.put_nowait(event)
        queue.put_nowait(credentials_event())
        hub.clients.add(queue)

        async def pump() -> None:
            while True:
                await sock.send_json(await queue.get())

        sender = asyncio.create_task(pump())
        try:
            while True:
                op = await sock.receive_json()
                if op.get("op") in ("setCredential", "clearCredential"):
                    handle_credential(op)
                else:
                    await engine.handle(op)
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(queue)
            sender.cancel()

    def handle_credential(op: dict[str, Any]) -> None:
        name = str(op.get("name", ""))
        try:
            if op["op"] == "setCredential":
                credentials.save(name, str(op.get("value", "")))
            else:
                credentials.clear(name)
        except ValueError as exc:
            hub.broadcast(credentials_event(str(exc)))
            return
        hub.broadcast(credentials_event())

    return app
