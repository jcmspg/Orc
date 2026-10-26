"""Pluggable model backends.

A provider turns a prompt into text. Two kinds exist:

* ``AnthropicProvider`` calls the Anthropic Messages API directly (API key billing).
* ``CLIProvider`` runs a coding-agent CLI in non-interactive mode, so it uses
  whatever that tool is logged into (Claude Code, Cursor, Copilot, Codex
  subscriptions). The CLI handles its own auth; orc never sees credentials.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

from orc import llm


class Provider(Protocol):
    name: str

    def complete(self, prompt: str, *, system: str | None = None) -> str: ...


@dataclass
class AnthropicProvider:
    client: object
    model: str = llm.DEFAULT_MODEL
    effort: str = "high"
    name: str = "anthropic"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        return llm.complete(
            self.client, prompt, system=system, model=self.model, effort=self.effort
        )


# Non-interactive invocations. The prompt is appended as the final argument.
CLI_PRESETS: dict[str, list[str]] = {
    "claude": ["claude", "-p"],
    "cursor": ["cursor-agent", "-p"],
    "copilot": ["copilot", "-s", "-p"],
    "codex": ["codex", "exec"],
}


@dataclass
class CLIProvider:
    argv: list[str]
    name: str = "cli"
    timeout: float = 600.0

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        # CLIs have no separate system channel in print mode, so prepend it.
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        result = subprocess.run(
            [*self.argv, full_prompt],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{self.argv[0]} exited {result.returncode}: {result.stderr.strip()}"
            )
        return result.stdout.strip()


def cli_provider(preset: str) -> CLIProvider:
    if preset not in CLI_PRESETS:
        raise ValueError(f"unknown CLI preset {preset!r}; choose from {sorted(CLI_PRESETS)}")
    return CLIProvider(argv=CLI_PRESETS[preset], name=preset)

