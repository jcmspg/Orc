"""Local server the desktop app talks to: one WebSocket, events out, ops in."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import signal
import sys
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from orc import credentials
from orc.engine import Emit, Engine
from orc.persist import BoardStore
from orc.runtimes import runtime_for
from orc import sandbox
from orc.sandbox import DockerSandbox

log = logging.getLogger(__name__)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
CLIENT_BACKLOG = 10_000  # events a client may fall behind by before it is cut off


class Client:
    """One connected app: its event queue, and a flag raised if it fell too far behind."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(CLIENT_BACKLOG)
        self.dropped = asyncio.Event()


class Hub:
    """Fans every event out to each connected client's queue, in order."""

    def __init__(self) -> None:
        self.clients: set[Client] = set()

    def broadcast(self, event: dict[str, Any]) -> None:
        for client in list(self.clients):
            try:
                client.queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled client must not grow memory without bound. It reconnects and gets a fresh snapshot.
                self.clients.discard(client)
                client.dropped.set()


def default_engine(emit: Emit) -> Engine:
    engine = Engine(emit, sandbox_factory=DockerSandbox.create, runtime_for=runtime_for, store=BoardStore())
    sandbox.image_notice = engine.hall_log  # a first-use image build is slow: say so in the chieftain's log
    return engine


def origin_allowed(origin: str | None) -> bool:
    """Block web pages: browsers let any site open a WebSocket to localhost.

    Allowed: no Origin (non-browser clients), the Electron app (`file://`, `null`)
    and local dev servers.
    """
    if origin in (None, "", "null", "file://"):
        return True
    parsed = urlparse(origin)
    return parsed.scheme in ("http", "https") and parsed.hostname in LOCAL_HOSTS


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        # os.kill(pid, 0) would TERMINATE the process on Windows, so ask the OS instead.
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.GetLastError() == 5  # access denied: exists, not ours
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists, owned by someone else: still alive
    return True


def stop_self() -> None:
    """Ask uvicorn to shut down as if we had been sent SIGTERM (which destroys the black boxes).

    raise_signal runs the installed handler in-process on every platform; os.kill(getpid(), SIGTERM)
    would hard-terminate us on Windows.
    """
    signal.raise_signal(signal.SIGTERM)


async def watch_parent(interval: float = 2.0) -> None:
    """If the desktop app that started us dies without stopping us, stop ourselves cleanly."""
    pid = int(os.environ.get("ORC_PARENT_PID") or 0)
    if not pid:
        return
    while True:
        await asyncio.sleep(interval)
        if not pid_alive(pid):
            stop_self()
            return


def watch_stdin() -> None:
    """With ORC_STOP_ON_STDIN_EOF=1, stop when stdin closes.

    The desktop app holds our stdin open; closing it is its portable "stop cleanly" request
    (Windows has no gentle SIGTERM), and it also fires if the app dies.
    """
    if not os.environ.get("ORC_STOP_ON_STDIN_EOF"):
        return
    loop = asyncio.get_running_loop()

    def wait() -> None:
        try:
            while sys.stdin.buffer.read(4096):
                pass
        except (OSError, ValueError):
            pass
        loop.call_soon_threadsafe(stop_self)

    threading.Thread(target=wait, daemon=True, name="orc-stdin").start()


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
        watch_stdin()
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
        client = Client()
        # Snapshot and subscribe with no await in between, so no event is missed.
        for event in engine.board.snapshot():
            client.queue.put_nowait(event)
        client.queue.put_nowait(credentials_event())
        hub.clients.add(client)

        async def pump() -> None:
            while True:
                await sock.send_json(await client.queue.get())

        async def cut_off() -> None:
            await client.dropped.wait()
            await sock.close(code=1013)  # try again later

        sender = asyncio.create_task(pump())
        watcher = asyncio.create_task(cut_off())
        try:
            while True:
                try:
                    op = await sock.receive_json()
                except ValueError:  # not JSON: ignore it and keep listening
                    continue
                if not isinstance(op, dict):
                    continue
                try:
                    if op.get("op") in ("setCredential", "clearCredential"):
                        handle_credential(op)
                    else:
                        await engine.handle(op)  # reports its own failures
                except Exception:
                    log.exception("op %r failed", op.get("op"))
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(client)
            sender.cancel()
            watcher.cancel()

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
        except OSError as exc:
            hub.broadcast(credentials_event(f"Couldn't save the credential: {exc.strerror or exc}"))
            return
        hub.broadcast(credentials_event())

    return app
