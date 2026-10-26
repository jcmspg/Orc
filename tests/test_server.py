from fastapi.testclient import TestClient

from orc.engine import Engine
from orc.runtimes import DummyRuntime
from orc.server import create_app
from tests.fakes import LocalSandbox


def fake_engine(emit):
    rt = DummyRuntime(delay=0)
    return Engine(emit, sandbox_factory=LocalSandbox.create, runtime_for=lambda b: rt, walk_s=0.01, flight_s=0.01)


def test_client_gets_snapshot_then_live_events():
    with TestClient(create_app(fake_engine)) as client, client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "board.reset"
        snapshot = [ws.receive_json() for _ in range(1 + 4 + 5)]  # repo, 4 orcs, 5 edges
        assert [e["type"] for e in snapshot].count("orc.spawned") == 4
        assert snapshot[0] == {"type": "board.repo", "repo": None}
        assert ws.receive_json()["type"] == "app.credentials"  # snapshot ends with credential status

        ws.send_json({"op": "spawn", "role": "tester", "backend": "claude"})
        spawned = ws.receive_json()
        assert spawned["type"] == "orc.spawned"
        assert spawned["orc"]["role"] == "tester"
        assert spawned["orc"]["hut"] == {"x": -150, "y": 199}  # fourth slot of the inner ring


def test_engine_stops_itself_when_the_app_dies(monkeypatch):
    import asyncio
    import os
    import signal
    import subprocess

    from orc import server

    dead = subprocess.Popen(["true"])
    dead.wait()  # a PID that no longer exists
    monkeypatch.setenv("ORC_PARENT_PID", str(dead.pid))
    sent = []
    real_kill = os.kill

    def fake_kill(pid, sig):
        if sig == signal.SIGTERM:
            sent.append(pid)
        else:
            real_kill(pid, sig)

    monkeypatch.setattr(server.os, "kill", fake_kill)
    asyncio.run(asyncio.wait_for(server.watch_parent(interval=0.01), timeout=2))
    assert sent == [os.getpid()]


def test_unknown_ops_are_ignored():
    with TestClient(create_app(fake_engine)) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"op": "spawn", "role": "dragon", "backend": "claude"})
        ws.send_json({"op": "nonsense"})
        assert client.get("/health").json() == {"status": "ok"}
