"""Git operations on the host repo. Only two ever touch it: reading HEAD
into a bundle, and fetching a finished branch back in as `orc/...`."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

URL_RE = re.compile(r"^(https?://|git@|ssh://)")


def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    path = base / "orc"
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_url(repo: str) -> bool:
    return bool(URL_RE.match(repo))


def _git(repo: str, *args: str) -> str:
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def validate_repo(repo: str) -> str:
    """Return a normalised repo reference, or raise ValueError."""
    repo = repo.strip()
    if not repo:
        raise ValueError("Empty repo path.")
    if is_url(repo):
        return repo
    path = Path(repo).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{path} is not a directory.")
    try:
        top = _git(str(path), "rev-parse", "--show-toplevel")
        _git(top, "rev-parse", "HEAD")
    except RuntimeError as exc:
        raise ValueError(f"{path} is not a git repo with at least one commit.") from exc
    return top


def bundle_head(repo: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "bundle", "create", str(out), "HEAD")


def fetch_branch(repo: str, bundle: Path, branch: str) -> None:
    _git(repo, "fetch", "--quiet", str(bundle), f"refs/heads/{branch}:refs/heads/{branch}")
