import asyncio
from pathlib import Path

import pytest

from orc.runtimes import ClaudeCodeRuntime, CursorRuntime, describe_cursor_tool, describe_tool

FIXTURE = Path(__file__).parent / "data" / "claude_stream.jsonl"


class ReplayBox:
    """Plays back a captured `claude -p --output-format stream-json` transcript."""

    name = "replay"

    def __init__(self, lines: list[str], code: int = 0) -> None:
        self.lines = lines
        self.code = code
        self.argv: list[str] = []
        self.env_names: tuple[str, ...] = ()

    async def exec(self, argv, *, env_names=(), on_line=None, check=True, workdir=None):
        self.argv, self.env_names = argv, env_names
        for line in self.lines:
            on_line(line)
        return self.code, "\n".join(self.lines)


def run(box, monkeypatch, token="t0k3n"):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if token:
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", token)
    else:
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    logs: list[tuple[str, str]] = []
    reply = asyncio.run(
        ClaudeCodeRuntime().run(box, "prompt", lambda t, lvl: logs.append((t, lvl)), kind="code", hints={})
    )
    return reply, logs


def test_parses_a_real_transcript(monkeypatch):
    box = ReplayBox(FIXTURE.read_text().splitlines())
    reply, logs = run(box, monkeypatch)
    assert reply.strip() == "done"
    assert ("$ echo pong", "cmd") in logs
    # Credentials are forwarded by name only, never as values on the command line.
    assert box.env_names == ("CLAUDE_CODE_OAUTH_TOKEN",)
    assert "t0k3n" not in " ".join(box.argv)
    assert "--dangerously-skip-permissions" in box.argv


def test_missing_credentials_explain_how_to_fix(monkeypatch):
    with pytest.raises(RuntimeError, match="claude setup-token"):
        run(ReplayBox([]), monkeypatch, token=None)


def test_failed_run_raises_with_detail(monkeypatch):
    lines = ['{"type":"result","is_error":true,"result":"Invalid API key"}']
    with pytest.raises(RuntimeError, match="Invalid API key"):
        run(ReplayBox(lines, code=1), monkeypatch)


# Event shapes from Cursor's stream-json docs (cursor.com/docs/cli/reference/output-format).
CURSOR_STREAM = [
    '{"type":"system","subtype":"init","apiKeySource":"env","cwd":"/home/node/work","session_id":"s","model":"auto","permissionMode":"force"}',
    '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"prompt"}]},"session_id":"s"}',
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Reading the file first."}]},"session_id":"s"}',
    '{"type":"tool_call","subtype":"started","call_id":"1","tool_call":{"readToolCall":{"args":{"path":"README.md"}}},"session_id":"s"}',
    '{"type":"tool_call","subtype":"completed","call_id":"1","tool_call":{"readToolCall":{"args":{"path":"README.md"},"result":{"success":{"content":"hi"}}}},"session_id":"s"}',
    '{"type":"tool_call","subtype":"started","call_id":"2","tool_call":{"writeToolCall":{"args":{"path":"src/a.ts","fileText":"x"}}},"session_id":"s"}',
    '{"type":"result","subtype":"success","duration_ms":1,"duration_api_ms":1,"is_error":false,"result":"Wrote src/a.ts.","session_id":"s"}',
]


def run_cursor(box, monkeypatch, key="cur-k3y"):
    if key:
        monkeypatch.setenv("CURSOR_API_KEY", key)
    else:
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    logs: list[tuple[str, str]] = []
    reply = asyncio.run(
        CursorRuntime().run(box, "prompt", lambda t, lvl: logs.append((t, lvl)), kind="code", hints={})
    )
    return reply, logs


def test_cursor_parses_stream(monkeypatch):
    box = ReplayBox(CURSOR_STREAM)
    reply, logs = run_cursor(box, monkeypatch)
    assert reply == "Wrote src/a.ts."
    assert ("read README.md", "cmd") in logs
    assert ("edit src/a.ts", "cmd") in logs
    assert ("Reading the file first.", "info") in logs
    assert len([1 for _, lvl in logs if lvl == "cmd"]) == 2  # completed events aren't logged twice
    assert box.env_names == ("CURSOR_API_KEY",)
    assert "cur-k3y" not in " ".join(box.argv)
    assert {"--force", "--trust"} <= set(box.argv)


def test_cursor_surfaces_auth_errors(monkeypatch):
    # Exactly what the real CLI prints in the box without a key.
    box = ReplayBox(["Error: Authentication required. Please run 'agent login' first, or set CURSOR_API_KEY environment variable."], code=1)
    with pytest.raises(RuntimeError, match="Authentication required"):
        run_cursor(box, monkeypatch)


def test_cursor_without_key_explains_where_to_get_one(monkeypatch):
    with pytest.raises(RuntimeError, match="cursor.com/dashboard/api"):
        run_cursor(ReplayBox([]), monkeypatch, key=None)


def test_cursor_tool_descriptions():
    assert describe_cursor_tool({"shellToolCall": {"args": {"command": "npm test"}}}) == "$ npm test"
    assert describe_cursor_tool({"editToolCall": {"args": {"path": "a.py"}}}) == "edit a.py"
    assert describe_cursor_tool({"grepToolCall": {"args": {"pattern": "TODO"}}}) == "search TODO"
    assert describe_cursor_tool({"function": {"name": "web_search", "arguments": "{}"}}) == "web_search"


def test_tool_descriptions():
    assert describe_tool("Bash", {"command": "npm test\nmore"}) == "$ npm test"
    assert describe_tool("Edit", {"file_path": "src/a.ts"}) == "edit src/a.ts"
    assert describe_tool("WebSearch", {}) == "WebSearch"
