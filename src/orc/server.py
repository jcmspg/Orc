"""Local server the desktop app talks to: one WebSocket, events out, ops in."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from orc.engine import Emit, Engine
from orc.runtimes import runtime_for
from orc.sandbox import DockerSandbox


class Hub:
    """Fans every event out to each connected client's queue, in order."""

    def __init__(self) -> None:
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()

    def broadcast(self, event: dict[str, Any]) -> None:
        for q in self.clients:
            q.put_nowait(event)


def default_engine(emit: Emit) -> Engine:
    return Engine(emit, sandbox_factory=DockerSandbox.create, runtime_for=runtime_for)


def create_app(engine_factory: Callable[[Emit], Engine] = default_engine) -> FastAPI:
    hub = Hub()
    state: dict[str, Engine] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        engine = engine_factory(hub.broadcast)
        engine.boot()
        state["engine"] = engine
        yield
        await engine.shutdown()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        engine = state["engine"]
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Snapshot and subscribe with no await in between, so no event is missed.
        for event in engine.board.snapshot():
            queue.put_nowait(event)
        hub.clients.add(queue)

        async def pump() -> None:
            while True:
                await sock.send_json(await queue.get())

        sender = asyncio.create_task(pump())
        try:
            while True:
                await engine.handle(await sock.receive_json())
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(queue)
            sender.cancel()

    return app
