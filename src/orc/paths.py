"""Per-user config, state and cache directories on Linux, macOS and Windows.

Linux keeps the XDG layout (and its `XDG_*` overrides, which tests rely on). The XDG
variables win on every platform when set, so a scratch directory always works.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _xdg(var: str) -> Path | None:
    value = os.environ.get(var)
    return Path(value) if value else None


def config_dir() -> Path:
    if xdg := _xdg("XDG_CONFIG_HOME"):
        return xdg / "orc"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "orc"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "orc"
    return Path.home() / ".config" / "orc"


def state_dir() -> Path:
    if xdg := _xdg("XDG_STATE_HOME"):
        return xdg / "orc"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "orc"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "orc"
    return Path.home() / ".local" / "state" / "orc"


def cache_dir() -> Path:
    if xdg := _xdg("XDG_CACHE_HOME"):
        return xdg / "orc"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "orc" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "orc"
    return Path.home() / ".cache" / "orc"
