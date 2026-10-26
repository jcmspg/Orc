import subprocess
from types import SimpleNamespace

import pytest

from orc import providers
from orc.providers import CLI_PRESETS, AnthropicProvider, CLIProvider, cli_provider


def test_cli_provider_appends_prompt_and_returns_stdout(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="  pong\n", stderr="")

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    out = cli_provider("claude").complete("ping")

    assert out == "pong"
    assert seen["argv"] == ["claude", "-p", "ping"]
    assert seen["kwargs"]["capture_output"] is True
    assert seen["kwargs"]["timeout"] == 600.0


def test_system_prompt_is_prepended_for_cli(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    CLIProvider(argv=["codex", "exec"]).complete("task", system="be terse")

    assert seen["argv"] == ["codex", "exec", "be terse\n\ntask"]


def test_nonzero_exit_raises_with_stderr(monkeypatch):
    monkeypatch.setattr(
        providers.subprocess,
        "run",
        lambda argv, **kw: SimpleNamespace(returncode=2, stdout="", stderr="not logged in"),
    )
    with pytest.raises(RuntimeError, match="not logged in"):
        cli_provider("cursor").complete("hi")


def test_unknown_preset_rejected():
    with pytest.raises(ValueError):
        cli_provider("nope")


def test_all_presets_are_known_tools():
    assert set(CLI_PRESETS) == {"claude", "cursor", "copilot", "codex"}


def test_anthropic_provider_delegates_to_llm(monkeypatch):
    calls = {}

    def fake_complete(client, prompt, **kwargs):
        calls["client"] = client
        calls["prompt"] = prompt
        calls.update(kwargs)
        return "reply"

    monkeypatch.setattr(providers.llm, "complete", fake_complete)
    provider = AnthropicProvider(client="C")

    assert provider.complete("hi", system="s") == "reply"
    assert calls["client"] == "C"
    assert calls["prompt"] == "hi"
    assert calls["system"] == "s"
    assert calls["model"] == providers.llm.DEFAULT_MODEL
