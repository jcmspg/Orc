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

        ws.send_json({"op": "spawn", "role": "tester", "backend": "claude"})
        spawned = ws.receive_json()
        assert spawned["type"] == "orc.spawned"
        assert spawned["orc"]["role"] == "tester"
        assert spawned["orc"]["hut"] == {"x": -150, "y": 199}  # fourth slot of the inner ring


def test_unknown_ops_are_ignored():
    with TestClient(create_app(fake_engine)) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"op": "spawn", "role": "dragon", "backend": "claude"})
        ws.send_json({"op": "nonsense"})
        assert client.get("/health").json() == {"status": "ok"}
