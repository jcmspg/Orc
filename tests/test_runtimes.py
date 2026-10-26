import asyncio
from pathlib import Path

import pytest

from orc.runtimes import ClaudeCodeRuntime, describe_tool

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


def test_tool_descriptions():
    assert describe_tool("Bash", {"command": "npm test\nmore"}) == "$ npm test"
    assert describe_tool("Edit", {"file_path": "src/a.ts"}) == "edit src/a.ts"
    assert describe_tool("WebSearch", {}) == "WebSearch"
