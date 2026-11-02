"""Agent runtimes: how each CLI is driven inside a black box.

`run` streams progress through `on_log(text, level)` and returns the agent's
final answer text (which the engine parses for plans and review verdicts).
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from typing import Any, Callable, Protocol

from orc.sandbox import Sandbox

LogFn = Callable[[str, str], None]


class Runtime(Protocol):
    name: str

    async def run(self, box: Sandbox, prompt: str, on_log: LogFn, *, kind: str, hints: dict[str, Any]) -> str: ...


class ClaudeCodeRuntime:
    """Claude Code in print mode. Full permissions are fine: it's a disposable box."""

    name = "claude"
    CREDENTIALS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")

    async def run(self, box: Sandbox, prompt: str, on_log: LogFn, *, kind: str, hints: dict[str, Any]) -> str:
        env = tuple(n for n in self.CREDENTIALS if os.environ.get(n))
        if not env:
            raise RuntimeError(
                "Claude Code has no credentials inside the black box. Run `claude setup-token` on your "
                "machine, then export CLAUDE_CODE_OAUTH_TOKEN (or ANTHROPIC_API_KEY) before starting orc."
            )
        result: dict[str, Any] = {}
        noise: list[str] = []

        def on_line(line: str) -> None:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                if line.strip():
                    noise.append(line)
                return
            if msg.get("type") == "assistant":
                for block in msg.get("message", {}).get("content", []):
                    if block.get("type") == "text" and block.get("text", "").strip():
                        on_log(_first_line(block["text"]), "info")
                    elif block.get("type") == "tool_use":
                        on_log(describe_tool(block.get("name", ""), block.get("input") or {}), "cmd")
            elif msg.get("type") == "result":
                result.update(msg)

        argv = [
            "claude", "-p", prompt,
            "--output-format", "stream-json", "--verbose",
            "--dangerously-skip-permissions",
        ]
        code, _ = await box.exec(argv, env_names=env, on_line=on_line, check=False)
        if code != 0 or result.get("is_error") or not result:
            detail = result.get("result") or "\n".join(noise[-5:]) or f"exit code {code}"
            raise RuntimeError(f"Claude Code failed: {_redact(_first_line(str(detail)), self.CREDENTIALS)}")
        return str(result.get("result", ""))


class CursorRuntime:
    """Cursor's agent CLI in print mode, authenticated with a user API key."""

    name = "cursor"
    CREDENTIALS = ("CURSOR_API_KEY",)

    async def run(self, box: Sandbox, prompt: str, on_log: LogFn, *, kind: str, hints: dict[str, Any]) -> str:
        if not os.environ.get("CURSOR_API_KEY"):
            raise RuntimeError(
                "Cursor has no credentials inside the black box. Create a user API key at "
                "https://cursor.com/dashboard/api, then export CURSOR_API_KEY before starting orc."
            )
        result: dict[str, Any] = {}
        noise: list[str] = []

        def on_line(line: str) -> None:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                if line.strip():
                    noise.append(line)  # errors arrive as plain text on stderr
                return
            kind_ = msg.get("type")
            if kind_ == "assistant":
                for block in msg.get("message", {}).get("content", []):
                    if block.get("type") == "text" and block.get("text", "").strip():
                        on_log(_first_line(block["text"]), "info")
            elif kind_ == "tool_call" and msg.get("subtype") == "started":
                on_log(describe_cursor_tool(msg.get("tool_call") or {}), "cmd")
            elif kind_ == "result":
                result.update(msg)

        argv = [
            "cursor-agent", "-p", prompt,
            "--output-format", "stream-json",
            # Run everything without prompts; the box is the sandbox, so Cursor's own is off.
            "--force", "--trust", "--sandbox", "disabled",
        ]
        code, _ = await box.exec(argv, env_names=self.CREDENTIALS, on_line=on_line, check=False)
        if code != 0 or result.get("is_error") or not result:
            detail = "\n".join(noise[-5:]) or result.get("result") or f"exit code {code}"
            raise RuntimeError(f"Cursor failed: {_redact(_first_line(str(detail)), self.CREDENTIALS)}")
        return str(result.get("result", ""))


class CodexRuntime:
    """OpenAI Codex CLI in exec mode, authenticated with an API key.

    A ChatGPT-plan login lives in ~/.codex/auth.json on the host, and host files never enter a
    box, so boxes use the API key that `codex exec` reads from CODEX_API_KEY.
    """

    name = "codex"
    CREDENTIALS = ("CODEX_API_KEY",)

    async def run(self, box: Sandbox, prompt: str, on_log: LogFn, *, kind: str, hints: dict[str, Any]) -> str:
        if not os.environ.get("CODEX_API_KEY"):
            raise RuntimeError(
                "Codex has no credentials inside the black box. Create an API key at "
                "https://platform.openai.com/api-keys, then export CODEX_API_KEY before starting orc "
                "(a ChatGPT login on your machine can't be shared with a box)."
            )
        answer: list[str] = []
        state: dict[str, Any] = {"done": False, "error": ""}
        noise: list[str] = []

        def on_line(line: str) -> None:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                if line.strip():
                    noise.append(line)
                return
            type_ = msg.get("type")
            item = msg.get("item") or {}
            if type_ == "item.started" and item.get("type") == "command_execution":
                on_log(f"$ {_first_line(unwrap_shell(str(item.get('command', ''))))}", "cmd")
            elif type_ == "item.completed":
                if item.get("type") == "agent_message" and str(item.get("text", "")).strip():
                    answer.append(str(item["text"]))
                    on_log(_first_line(item["text"]), "info")
                elif item.get("type") == "file_change":
                    for change in item.get("changes") or []:
                        on_log(f"edit {_relative(str(change.get('path', '')))}", "cmd")
                elif item.get("type") in ("mcp_tool_call", "web_search"):
                    on_log(describe_codex_item(item), "cmd")
            elif type_ == "turn.completed":
                state["done"] = True
            elif type_ == "turn.failed":
                state["error"] = str((msg.get("error") or {}).get("message", "turn failed"))
            elif type_ == "error":
                # Transient ("Reconnecting...") until a turn.failed says otherwise.
                state["error"] = state["error"] or str(msg.get("message", ""))

        argv = [
            "codex", "exec", "--json",
            # No approvals and no Codex sandbox: the box is the sandbox.
            "--dangerously-bypass-approvals-and-sandbox",
            "--", prompt,
        ]
        code, _ = await box.exec(argv, env_names=self.CREDENTIALS, on_line=on_line, check=False)
        if code != 0 or not state["done"]:
            detail = state["error"] or "\n".join(noise[-5:]) or f"exit code {code}"
            raise RuntimeError(f"Codex failed: {_redact(_first_line(detail, 300), self.CREDENTIALS)}")
        return answer[-1] if answer else ""


class CopilotRuntime:
    """GitHub Copilot CLI in prompt mode, authenticated with a GitHub token."""

    name = "copilot"
    CREDENTIALS = ("COPILOT_GITHUB_TOKEN",)

    async def run(self, box: Sandbox, prompt: str, on_log: LogFn, *, kind: str, hints: dict[str, Any]) -> str:
        if not os.environ.get("COPILOT_GITHUB_TOKEN"):
            raise RuntimeError(
                "Copilot has no credentials inside the black box. Create a fine-grained personal access token "
                'with the "Copilot Requests" permission at https://github.com/settings/personal-access-tokens/new, '
                "then export COPILOT_GITHUB_TOKEN before starting orc."
            )
        answer: list[str] = []
        result: dict[str, Any] = {}
        errors: list[str] = []
        noise: list[str] = []

        def on_line(line: str) -> None:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                if line.strip():
                    noise.append(line)  # auth and startup errors arrive as plain text
                return
            type_ = str(msg.get("type", ""))
            data = msg.get("data") or {}
            if type_ == "assistant.message" and str(data.get("content", "")).strip():
                answer.append(str(data["content"]))
                on_log(_first_line(data["content"]), "info")
            elif type_ == "tool.execution_start":
                on_log(describe_copilot_tool(str(data.get("toolName", "")), data.get("arguments")), "cmd")
            elif type_ == "result":
                result.update(msg)
            elif type_.endswith("error") and data.get("message"):
                errors.append(str(data["message"]))

        argv = [
            "copilot", "-p", prompt,
            "--output-format", "json",
            # Every tool, path and URL without prompts; the box is the sandbox.
            "--allow-all", "--no-ask-user",
            # Keep the box self-contained: no built-in GitHub MCP server acting on the user's account.
            "--disable-builtin-mcps",
            "--no-auto-update",
            "--secret-env-vars=COPILOT_GITHUB_TOKEN",
        ]
        code, _ = await box.exec(argv, env_names=self.CREDENTIALS, on_line=on_line, check=False)
        if code != 0 or not result or result.get("exitCode", 0) != 0:
            plain = [ln for ln in noise if ln.strip().startswith("Error")] or noise[-5:]
            detail = (errors[-1:] or ["\n".join(plain)])[0] or f"exit code {code}"
            raise RuntimeError(f"Copilot failed: {_redact(_first_line(detail, 300), self.CREDENTIALS)}")
        return answer[-1] if answer else ""


class UnavailableRuntime:
    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, box: Sandbox, prompt: str, on_log: LogFn, *, kind: str, hints: dict[str, Any]) -> str:
        raise RuntimeError(f"{self.name} is not installed in the black box yet. Train this peon with claude for now.")


class DummyRuntime:
    """Spends no model calls: makes a trivial commit, rejects a first review.

    Enabled with ORC_FAKE_AGENT=1 to exercise boxes, git and the UI end to end.
    """

    name = "dummy"

    def __init__(self, delay: float = 1.5) -> None:
        self.delay = delay

    async def run(self, box: Sandbox, prompt: str, on_log: LogFn, *, kind: str, hints: dict[str, Any]) -> str:
        await asyncio.sleep(self.delay)
        if kind == "plan":
            verbs = ["Build the core", "Wire it up", "Handle errors", "Polish"]
            tasks = [
                {"worker": w, "task": f"{verbs[i % len(verbs)]}: {hints['task']}"}
                for i, w in enumerate(hints["workers"])
            ]
            return json.dumps({"tasks": tasks})
        if kind == "code":
            on_log("$ echo change >> ORC_DUMMY.md", "cmd")
            await box.exec(["sh", "-c", 'echo "- dummy change $(date -u +%FT%TZ)" >> ORC_DUMMY.md'])
            await asyncio.sleep(self.delay)
            return "Appended a line to ORC_DUMMY.md."
        if kind == "review":
            approve = hints.get("attempt", 0) >= 1
            findings = [] if approve else ["Dummy reviewer wants one more pass."]
            return json.dumps({"approve": approve, "findings": findings})
        return ""


def runtime_for(backend: str) -> Runtime:
    if os.environ.get("ORC_FAKE_AGENT"):
        return DummyRuntime()
    if backend == "claude":
        return ClaudeCodeRuntime()
    if backend == "cursor":
        return CursorRuntime()
    if backend == "codex":
        return CodexRuntime()
    if backend == "copilot":
        return CopilotRuntime()
    return UnavailableRuntime(backend)


def describe_tool(name: str, args: dict[str, Any]) -> str:
    if name == "Bash":
        return f"$ {_first_line(str(args.get('command', '')))}"
    if name in ("Edit", "MultiEdit", "Write"):
        return f"edit {args.get('file_path', '')}"
    if name == "Read":
        return f"read {args.get('file_path', '')}"
    if name in ("Grep", "Glob"):
        return f"search {args.get('pattern', '')}"
    return name


def describe_cursor_tool(call: dict[str, Any]) -> str:
    """Cursor wraps each call as {"<name>ToolCall": {"args": {...}}} or {"function": {...}}."""
    if "function" in call:
        return str(call["function"].get("name", "tool"))
    for key, body in call.items():
        args = (body or {}).get("args") or {}
        name = key.removesuffix("ToolCall")
        if "command" in args:
            return f"$ {_first_line(str(args['command']))}"
        if "path" in args:
            verb = "read" if name.startswith("read") else "search" if name in ("grep", "glob") else "edit"
            return f"{verb} {args['path']}"
        if "pattern" in args:
            return f"search {args['pattern']}"
        return name
    return "tool"


WORKDIR_PREFIX = "/home/node/work/"


def unwrap_shell(command: str) -> str:
    """Codex reports commands as `/bin/bash -lc "<script>"`; show just the script."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if len(parts) == 3 and parts[0].rsplit("/", 1)[-1] in ("bash", "sh", "zsh") and parts[1] in ("-lc", "-c"):
        return parts[2]
    return command


def describe_codex_item(item: dict[str, Any]) -> str:
    if item.get("type") == "mcp_tool_call":
        return f"{item.get('server', 'mcp')}.{item.get('tool', 'tool')}"
    if item.get("type") == "web_search":
        return f"search {item.get('query', '')}"
    return str(item.get("type", "tool"))


def describe_copilot_tool(name: str, args: Any) -> str:
    """Copilot's tools: bash takes a command, apply_patch a raw patch string, the rest mostly a path."""
    if isinstance(args, str):
        files = [
            _relative(ln.split(":", 1)[1].strip())
            for ln in args.splitlines()
            if ln.startswith(("*** Add File:", "*** Update File:", "*** Delete File:"))
        ]
        return f"edit {', '.join(files)}" if files else name
    args = args if isinstance(args, dict) else {}
    if "command" in args:
        return f"$ {_first_line(str(args['command']))}"
    path = args.get("path") or args.get("file_path")
    if path:
        verb = "read" if name in ("view", "read", "read_file") else "edit"
        return f"{verb} {_relative(str(path))}"
    if "pattern" in args:
        return f"search {args['pattern']}"
    return name or "tool"


def _relative(path: str) -> str:
    return path.removeprefix(WORKDIR_PREFIX)


def _redact(text: str, names: tuple[str, ...]) -> str:
    """Some APIs echo a rejected key back in their error; never let a value reach the logs."""
    for n in names:
        value = os.environ.get(n)
        if value and len(value) >= 4:
            text = text.replace(value, f"${n}")
    return text


def _first_line(text: str, limit: int = 160) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"
