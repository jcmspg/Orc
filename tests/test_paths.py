import asyncio
import sys
from pathlib import Path

import pytest

from orc import gitops, paths, sandbox


def test_xdg_overrides_win_everywhere(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "k"))
    assert paths.config_dir() == tmp_path / "c" / "orc"
    assert paths.state_dir() == tmp_path / "s" / "orc"
    assert paths.cache_dir() == tmp_path / "k" / "orc"


@pytest.mark.parametrize(
    "platform, expected",
    [
        ("win32", ("AppData", "Roaming")),
        ("darwin", ("Library", "Application Support")),
        ("linux", (".config",)),
    ],
)
def test_platform_defaults(monkeypatch, tmp_path, platform, expected):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sys, "platform", platform)
    assert paths.config_dir() == tmp_path.joinpath(*expected, "orc")


def test_windows_uses_appdata_env(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths.state_dir() == tmp_path / "orc"


def test_missing_docker_and_git_are_explained(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(sandbox.SandboxError, match="Docker isn't installed"):
        asyncio.run(sandbox.DockerSandbox.create("x"))
    assert asyncio.run(sandbox.image_exists()) is False
    with pytest.raises(RuntimeError, match="git isn't installed"):
        gitops._git("/tmp", "status")
