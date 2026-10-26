"""Agent runtimes: how each CLI is driven inside a black box.

`run` streams progress through `on_log(text, level)` and returns the agent's
final answer text (which the engine parses for plans and review verdicts).
"""

from __future__ import annotations

import asyncio
import json
import os
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
            raise RuntimeError(f"Claude Code failed: {_first_line(str(detail))}")
        return str(result.get("result", ""))


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


def _first_line(text: str, limit: int = 160) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"
