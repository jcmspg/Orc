import os
import stat

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from orc import credentials
from orc.server import create_app, origin_allowed
from tests.test_server import fake_engine

CLAUDE = "sk-ant-oat01-" + "a" * 40  # fake, shaped like a real token


def test_save_writes_private_file_and_takes_effect():
    credentials.save("CLAUDE_CODE_OAUTH_TOKEN", f"  {CLAUDE}\n")
    path = credentials.env_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text() == f"export CLAUDE_CODE_OAUTH_TOKEN={CLAUDE}\n"
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == CLAUDE
    assert credentials.status()["CLAUDE_CODE_OAUTH_TOKEN"] == "saved"


def test_save_replaces_its_line_and_keeps_others():
    path = credentials.env_path()
    path.parent.mkdir(parents=True)
    path.write_text("# mine\nexport OTHER=1\nexport CURSOR_API_KEY=old\n")
    credentials.save("CURSOR_API_KEY", "key_new")
    assert path.read_text() == "# mine\nexport OTHER=1\nexport CURSOR_API_KEY=key_new\n"


def test_values_needing_quotes_round_trip(monkeypatch):
    credentials.save("CURSOR_API_KEY", "abc$def'g")
    monkeypatch.delenv("CURSOR_API_KEY")
    monkeypatch.setattr(credentials, "_sources", {})
    credentials.load_into_environ()
    assert os.environ["CURSOR_API_KEY"] == "abc$def'g"


def test_real_environment_wins_over_the_file(monkeypatch):
    credentials.save("CURSOR_API_KEY", "from_file")
    monkeypatch.setenv("CURSOR_API_KEY", "from_env")
    monkeypatch.setattr(credentials, "_sources", {})
    credentials.load_into_environ()
    assert os.environ["CURSOR_API_KEY"] == "from_env"
    assert credentials.status()["CURSOR_API_KEY"] == "env"


def test_clear_removes_saved_value():
    credentials.save("CURSOR_API_KEY", "k")
    credentials.clear("CURSOR_API_KEY")
    assert "CURSOR_API_KEY" not in os.environ
    assert "CURSOR_API_KEY" not in credentials.env_path().read_text()
    assert credentials.status()["CURSOR_API_KEY"] == "missing"


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("PATH", "x", "Unknown"),
        ("CURSOR_API_KEY", "   ", "Paste"),
        ("CURSOR_API_KEY", "two words", "spaces"),
        ("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-api03-x", "sk-ant-oat"),
    ],
)
def test_rejects_bad_input(name, value, message):
    with pytest.raises(ValueError, match=message):
        credentials.save(name, value)
    assert not credentials.env_path().exists()


def test_origin_policy():
    assert origin_allowed(None)
    assert origin_allowed("file://")
    assert origin_allowed("null")
    assert origin_allowed("http://localhost:5173")
    assert origin_allowed("http://127.0.0.1:47820")
    assert not origin_allowed("https://evil.example")
    assert not origin_allowed("http://localhost.evil.example")


def drain_snapshot(ws):
    while True:
        event = ws.receive_json()
        if event["type"] == "app.credentials":
            return event


def test_credentials_over_the_socket_never_echo_values():
    with TestClient(create_app(fake_engine, session_token="")) as client, client.websocket_connect("/ws") as ws:
        first = drain_snapshot(ws)
        assert first["status"]["CLAUDE_CODE_OAUTH_TOKEN"] == "missing"
        ws.send_json({"op": "setCredential", "name": "CLAUDE_CODE_OAUTH_TOKEN", "value": CLAUDE})
        event = ws.receive_json()
        assert event == {"type": "app.credentials", "status": {**first["status"], "CLAUDE_CODE_OAUTH_TOKEN": "saved"}}
        ws.send_json({"op": "setCredential", "name": "CLAUDE_CODE_OAUTH_TOKEN", "value": "nope"})
        error = ws.receive_json()
        assert "sk-ant-oat" in error["error"] and CLAUDE not in str(error)


def test_session_token_and_origin_are_enforced():
    app = create_app(fake_engine, session_token="s3cret")
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?token=wrong") as ws:
                ws.receive_json()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?token=s3cret", headers={"origin": "https://evil.example"}) as ws:
                ws.receive_json()
        with client.websocket_connect("/ws?token=s3cret") as ws:
            assert ws.receive_json()["type"] == "board.reset"
