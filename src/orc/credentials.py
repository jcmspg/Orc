"""Agent credentials for the black boxes, saved in a private env file.

The file (`~/.config/orc/env`, mode 600) holds `export NAME=value` lines, so it
can also be sourced by a shell. orc loads it at startup; values set in the real
environment win. Values are written here and forwarded to boxes by name; they
are never sent to the app or logged.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

# Only these names can be saved, so the app can't be used to write arbitrary variables.
KNOWN = {
    "CLAUDE_CODE_OAUTH_TOKEN": "Claude subscription token",
    "ANTHROPIC_API_KEY": "Anthropic API key",
    "CURSOR_API_KEY": "Cursor API key",
}

LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*)$")

# Where each known variable came from at startup: "env" (the real environment) or "saved".
_sources: dict[str, str] = {}


def env_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "orc" / "env"


def _read() -> dict[str, str]:
    path = env_path()
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        m = LINE_RE.match(line)
        if m:
            try:
                parts = shlex.split(m.group(2))
            except ValueError:
                continue
            values[m.group(1)] = parts[0] if parts else ""
    return values


def load_into_environ() -> None:
    """At startup: fill in known variables from the file unless the environment already has them."""
    saved = _read()
    for name in KNOWN:
        if os.environ.get(name):
            _sources[name] = "env"
        elif saved.get(name):
            os.environ[name] = saved[name]
            _sources[name] = "saved"


def status() -> dict[str, str]:
    """`env`, `saved` or `missing` for each known variable. Never the values."""
    out: dict[str, str] = {}
    for name in KNOWN:
        if not os.environ.get(name):
            out[name] = "missing"
        else:
            out[name] = _sources.get(name, "env")
    return out


def validate(name: str, value: str) -> str:
    if name not in KNOWN:
        raise ValueError(f"Unknown credential {name}.")
    value = value.strip()
    if not value:
        raise ValueError("Paste a value first.")
    if any(c.isspace() for c in value) or len(value) > 4096:
        raise ValueError("That doesn't look like a token: it contains spaces or line breaks.")
    if name == "CLAUDE_CODE_OAUTH_TOKEN" and not value.startswith("sk-ant-oat"):
        raise ValueError("Claude subscription tokens start with sk-ant-oat. Get one with `claude setup-token`.")
    if name == "ANTHROPIC_API_KEY" and not value.startswith("sk-ant-api"):
        raise ValueError("Anthropic API keys start with sk-ant-api.")
    return value


def save(name: str, value: str) -> None:
    """Write or replace one `export` line, keep every other line, and use it right away."""
    value = validate(name, value)
    _rewrite(name, f"export {name}={shlex.quote(value)}")
    os.environ[name] = value
    _sources[name] = "saved"


def clear(name: str) -> None:
    """Remove a saved value. A value from the real environment can't be cleared from here."""
    if name not in KNOWN:
        raise ValueError(f"Unknown credential {name}.")
    _rewrite(name, None)
    if _sources.get(name) == "saved":
        os.environ.pop(name, None)
        _sources.pop(name, None)


def _rewrite(name: str, new_line: str | None) -> None:
    path = env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [ln for ln in lines if not ((m := LINE_RE.match(ln)) and m.group(1) == name)]
    if new_line:
        kept.append(new_line)
    tmp = path.with_suffix(".tmp")
    # Create private from the start: the secret is never on disk with wider permissions.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(kept) + ("\n" if kept else ""))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
