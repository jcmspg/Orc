import asyncio
from pathlib import Path

import pytest

from orc.runtimes import (
    ClaudeCodeRuntime,
    CodexRuntime,
    CopilotRuntime,
    CursorRuntime,
    describe_copilot_tool,
    describe_cursor_tool,
    describe_tool,
    unwrap_shell,
)

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


# Real `codex exec --json` output (codex-cli 0.164): the actual CLI, driven by a scripted stand-in for the
# OpenAI Responses API because no OpenAI login was available. Paths mapped to the box workdir.
CODEX_FIXTURE = Path(__file__).parent / "data" / "codex_stream.jsonl"

# Real codex output in a box with no key: transient errors, then turn.failed (cf-ray/request ids trimmed).
CODEX_NO_AUTH = [
    "Reading additional input from stdin...",
    '{"type":"thread.started","thread_id":"00000000-0000-0000-0000-000000000000"}',
    '{"type":"turn.started"}',
    '{"type":"error","message":"Reconnecting... 5/5 (unexpected status 401 Unauthorized: Missing bearer or basic authentication in header, url: https://api.openai.com/v1/responses)"}',
    '{"type":"turn.failed","error":{"message":"unexpected status 401 Unauthorized: Missing bearer or basic authentication in header, url: https://api.openai.com/v1/responses"}}',
]


def run_codex(box, monkeypatch, key="sk-c0dex"):
    if key:
        monkeypatch.setenv("CODEX_API_KEY", key)
    else:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
    logs: list[tuple[str, str]] = []
    reply = asyncio.run(
        CodexRuntime().run(box, "prompt", lambda t, lvl: logs.append((t, lvl)), kind="code", hints={})
    )
    return reply, logs


def test_codex_parses_a_real_transcript(monkeypatch):
    box = ReplayBox(CODEX_FIXTURE.read_text().splitlines())
    reply, logs = run_codex(box, monkeypatch)
    assert reply == "Created hello.txt containing hi and added notes.txt."
    assert logs == [
        ("I'll create the file, then show it.", "info"),
        ("$ printf 'hi\\n' > hello.txt && cat hello.txt", "cmd"),  # started and completed log once
        ("edit notes.txt", "cmd"),
        (reply, "info"),
    ]
    assert box.env_names == ("CODEX_API_KEY",)
    assert "sk-c0dex" not in " ".join(box.argv)
    assert "--dangerously-bypass-approvals-and-sandbox" in box.argv
    assert box.argv[-2:] == ["--", "prompt"]  # a prompt starting with "-" is never read as a flag


def test_codex_surfaces_auth_errors(monkeypatch):
    with pytest.raises(RuntimeError, match="401 Unauthorized"):
        run_codex(ReplayBox(CODEX_NO_AUTH, code=1), monkeypatch)


def test_codex_never_logs_the_key(monkeypatch):
    # The API echoes a rejected key back in its error message.
    lines = ['{"type":"turn.failed","error":{"message":"Incorrect API key provided: sk-c0dex."}}']
    with pytest.raises(RuntimeError) as err:
        run_codex(ReplayBox(lines, code=1), monkeypatch)
    assert "sk-c0dex" not in str(err.value)
    assert "$CODEX_API_KEY" in str(err.value)


def test_codex_without_key_explains_where_to_get_one(monkeypatch):
    with pytest.raises(RuntimeError, match="platform.openai.com/api-keys"):
        run_codex(ReplayBox([]), monkeypatch, key=None)


# Real `copilot -p ... --output-format json` output (Copilot CLI 1.0.103), captured with a host login and
# only `write` and `shell(cat)` allowed, so its first command was denied. Streaming deltas, MCP status,
# usage and response ids stripped; paths mapped to the box workdir.
COPILOT_FIXTURE = Path(__file__).parent / "data" / "copilot_stream.jsonl"


def run_copilot(box, monkeypatch, token="github_pat_c0p"):
    if token:
        monkeypatch.setenv("COPILOT_GITHUB_TOKEN", token)
    else:
        monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
    logs: list[tuple[str, str]] = []
    reply = asyncio.run(
        CopilotRuntime().run(box, "prompt", lambda t, lvl: logs.append((t, lvl)), kind="code", hints={})
    )
    return reply, logs


def test_copilot_parses_a_real_transcript(monkeypatch):
    box = ReplayBox(COPILOT_FIXTURE.read_text().splitlines())
    reply, logs = run_copilot(box, monkeypatch)
    assert reply == "Created `hello.txt` containing `hi` and ran `cat hello.txt`, which output `hi`."
    assert logs == [
        ("$ printf 'hi\\n' > hello.txt && cat hello.txt", "cmd"),
        ("edit hello.txt", "cmd"),
        ("$ cat hello.txt", "cmd"),
        (reply, "info"),
    ]
    assert box.env_names == ("COPILOT_GITHUB_TOKEN",)
    assert "github_pat_c0p" not in " ".join(box.argv)
    assert {"--allow-all", "--no-ask-user"} <= set(box.argv)


def test_copilot_surfaces_auth_errors(monkeypatch):
    # What the real CLI prints in the box without a token.
    lines = [
        "Error: No authentication information found.",
        "",
        "Copilot can be authenticated with GitHub using an OAuth Token or a Fine-Grained Personal Access Token.",
        "",
        "To authenticate, you can use any of the following methods:",
        "  • Set the COPILOT_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN environment variable",
    ]
    with pytest.raises(RuntimeError, match="No authentication information found"):
        run_copilot(ReplayBox(lines, code=1), monkeypatch)


def test_copilot_failed_result_raises(monkeypatch):
    lines = ['{"type":"session.error","data":{"message":"Quota exceeded"}}', '{"type":"result","exitCode":1}']
    with pytest.raises(RuntimeError, match="Quota exceeded"):
        run_copilot(ReplayBox(lines, code=1), monkeypatch)


def test_copilot_without_token_explains_where_to_get_one(monkeypatch):
    with pytest.raises(RuntimeError, match="personal-access-tokens"):
        run_copilot(ReplayBox([]), monkeypatch, token=None)


def test_codex_and_copilot_tool_descriptions():
    assert unwrap_shell("/bin/bash -lc 'npm test'") == "npm test"
    assert unwrap_shell("ls -la") == "ls -la"
    assert describe_copilot_tool("view", {"path": "/home/node/work/src/a.py"}) == "read src/a.py"
    assert describe_copilot_tool("edit", {"path": "a.py", "old_str": "x"}) == "edit a.py"
    patch = "*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** End Patch\n"
    assert describe_copilot_tool("apply_patch", patch) == "edit a.py"
    assert describe_copilot_tool("report_intent", {"intent": "Fixing"}) == "report_intent"
