"""Git operations on the host repo. This is the only module that touches it.

Reads: HEAD into a bundle, branch tips, diffs, the worktree list. Writes, and
only these: fetching a finished branch in as `orc/...`, and, for an in-app
merge, new objects plus a compare-and-swap of a branch ref that no worktree
has checked out. Nothing here ever checks out, resets, or writes a working
tree or an index.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from collections.abc import Iterable
from pathlib import Path

from orc import paths


GIT_MISSING = "git isn't installed (or isn't on PATH). Install it from https://git-scm.com/downloads and restart orc."
URL_RE = re.compile(r"^(https?://|git@|ssh://)")
DIFF_LIMIT = 400_000  # bytes of diff sent to the app; anything past this is cut and flagged
HEAD_BUNDLE_REUSE_S = 600  # jobs starting from the same commit within this share one bundle of it
BUNDLE_MAX_AGE_S = 3600  # unreferenced bundles older than this are swept


def cache_dir() -> Path:
    path = paths.cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundles_dir() -> Path:
    path = cache_dir() / "bundles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_url(repo: str) -> bool:
    return bool(URL_RE.match(repo))


def _run(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise RuntimeError(GIT_MISSING) from None


def _git(repo: str, *args: str) -> str:
    result = _run(repo, *args)
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


def head_branch(repo: str) -> str | None:
    """The branch HEAD points at, or None when HEAD is detached."""
    # Not --short: with a tag of the same name it prints "heads/main", which is no branch name.
    result = _run(repo, "symbolic-ref", "-q", "HEAD")
    ref = result.stdout.strip() if result.returncode == 0 else ""
    return ref.removeprefix("refs/heads/") or None


def bundle_head(repo: str, out: Path) -> str | None:
    """Bundle HEAD for a box to clone. Returns the branch HEAD was on (None if detached)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    branch = head_branch(repo)
    _git(repo, "bundle", "create", str(out), "HEAD")
    return branch


def head_bundle(repo: str) -> tuple[Path, str | None]:
    """A bundle of HEAD for a box to clone, shared by jobs that start from the same commit.

    Bundling walks the whole history, so on a big repo it is worth doing once for
    the plan, every coder and every reviewer. Returns the bundle and the branch HEAD is on.
    """
    sha = _git(repo, "rev-parse", "HEAD")
    path = bundles_dir() / f"head-{sha}.bundle"
    branch = head_branch(repo)
    try:
        if time.time() - path.stat().st_mtime < HEAD_BUNDLE_REUSE_S:
            return path, branch
    except OSError:
        pass
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        bundle_head(repo, tmp)
        os.replace(tmp, path)  # atomic: a concurrent job sees the old bundle or the new one, never half of one
    finally:
        tmp.unlink(missing_ok=True)
    prune_bundles(prefix="head-")
    return path, branch


def prune_bundles(keep: Iterable[str | Path] = (), *, prefix: str = "", max_age_s: float = BUNDLE_MAX_AGE_S) -> int:
    """Delete bundles older than `max_age_s` that aren't in `keep`. Returns how many went."""
    kept = {Path(k).resolve() for k in keep}
    now = time.time()
    removed = 0
    for f in bundles_dir().iterdir():
        try:
            if not f.name.startswith(prefix) or f.resolve() in kept or now - f.stat().st_mtime < max_age_s:
                continue
            f.unlink()
            removed += 1
        except OSError:
            pass  # gone already, or not ours to remove
    return removed


def fetch_branch(repo: str, bundle: Path, branch: str) -> None:
    _git(repo, "fetch", "--quiet", str(bundle), f"refs/heads/{branch}:refs/heads/{branch}")


def branch_tip(repo: str, branch: str) -> str | None:
    """The commit `refs/heads/<branch>` points at, or None if there is no such branch."""
    result = _run(repo, "rev-parse", "-q", "--verify", f"refs/heads/{branch}^{{commit}}")
    return (result.stdout.strip() or None) if result.returncode == 0 else None


def has_commit(repo: str, sha: str) -> bool:
    return _run(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def is_ancestor(repo: str, older: str, newer: str) -> bool:
    return _run(repo, "merge-base", "--is-ancestor", older, newer).returncode == 0


def checked_out_at(repo: str, branch: str) -> list[str]:
    """Worktrees of the repo (the main one included) that have `branch` checked out."""
    paths: list[str] = []
    current = ""
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}":
            paths.append(current)
    return paths


def diff(repo: str, base: str, head: str, limit: int = DIFF_LIMIT) -> tuple[str, bool]:
    """`git diff base...head`, from the object store only (never the working tree).

    Returns (text, truncated). At most `limit` bytes are read, so a huge diff
    costs neither memory nor a giant message to the app.
    """
    argv = ["git", "-C", repo, "diff", "--no-color", "--no-ext-diff", "--no-textconv", f"{base}...{head}", "--"]
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        assert proc.stdout is not None and proc.stderr is not None
        data = proc.stdout.read(limit + 1)
        truncated = len(data) > limit
        if truncated:
            proc.kill()
            data = data[:limit]
            cut = data.rfind(b"\n")
            data = data[: cut + 1] if cut >= 0 else data  # end on a whole line
        err = proc.stderr.read()
        code = proc.wait()
    if not truncated and code != 0:
        raise RuntimeError(f"git diff failed: {err.decode(errors='replace').strip()}")
    return data.decode(errors="replace"), truncated


def diff_bundle(bundle: Path, base: str, head: str, limit: int = DIFF_LIMIT) -> tuple[str, bool]:
    """Diff a delivered bundle with no user repo involved: unpack it into a scratch bare repo."""
    with tempfile.TemporaryDirectory(prefix="orc-diff-", dir=cache_dir()) as tmp:
        _git(tmp, "init", "-q", "--bare")
        _git(tmp, "fetch", "-q", str(bundle), "refs/heads/*:refs/heads/*")
        return diff(tmp, base, head, limit)


@dataclass
class MergeResult:
    """What an in-app merge did.

    status: `merged` (`commit` is the new tip of the target), `checkedOut` (nothing
    moved; `worktrees` have the target checked out; `conflicts` lists the files a
    merge there will conflict in), `conflict` (nothing moved; `conflicts` lists the
    files), `upToDate` (the target already contains the branch).
    """

    status: str
    commit: str | None = None
    conflicts: list[str] = field(default_factory=list)
    worktrees: list[str] = field(default_factory=list)


def _commit_tree(repo: str, tree: str, target: str, tip: str, message: str) -> str:
    """Make the merge commit as the user, or as "orc" when git has no identity to use.

    Without this a machine that never ran `git config user.email` couldn't merge from the app.
    """
    env = os.environ.copy()
    if _run(repo, "var", "GIT_COMMITTER_IDENT").returncode != 0:
        for who in ("AUTHOR", "COMMITTER"):
            env.setdefault(f"GIT_{who}_NAME", "orc")
            env.setdefault(f"GIT_{who}_EMAIL", "orc@localhost")
    result = subprocess.run(
        ["git", "-C", repo, "commit-tree", tree, "-p", target, "-p", tip, "-m", message],
        capture_output=True, text=True, check=False, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git commit-tree failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def merge(repo: str, branch: str, into: str, *, expect: str | None = None) -> MergeResult:
    """Merge `branch` into `into` with plumbing only.

    `git merge-tree --write-tree` computes the merged tree (and any conflicts)
    in the object store, `git commit-tree` makes the merge commit with both
    parents, and `git update-ref <new> <old>` moves `into` only if it still
    points where we read it, so a concurrent move fails instead of being lost.
    Moves nothing if `into` is checked out in any worktree: that worktree's
    files and index would silently fall out of step with its branch.
    `expect`, if given, is the commit `branch` must still be at.
    """
    tip = branch_tip(repo, branch)
    if tip is None:
        raise RuntimeError(f"Branch {branch} doesn't exist.")
    if expect and tip != expect:
        raise RuntimeError(f"Branch {branch} is at {tip[:8]}, not {expect[:8]} as delivered.")
    target = branch_tip(repo, into)
    if target is None:
        raise RuntimeError(f"Branch {into} doesn't exist.")
    if is_ancestor(repo, tip, target):
        return MergeResult("upToDate", commit=target)

    # Only writes objects; tells us the merged tree, or which files would conflict.
    result = _run(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", target, tip)
    lines = result.stdout.splitlines()
    if result.returncode not in (0, 1) or not lines:
        detail = result.stderr.strip() or result.stdout.strip()
        if "write-tree" in detail or "usage:" in detail:
            detail = "this git is too old for in-app merges (needs git 2.38 or newer); merge it yourself"
        raise RuntimeError(f"git merge-tree failed: {detail}")
    conflicts = sorted({line for line in lines[1:] if line.strip()}) if result.returncode == 1 else []
    worktrees = checked_out_at(repo, into)
    if worktrees:
        return MergeResult("checkedOut", conflicts=conflicts, worktrees=worktrees)
    if conflicts:
        return MergeResult("conflict", conflicts=conflicts)
    message = f"Merge branch '{branch}' into {into}"
    commit = _commit_tree(repo, lines[0].strip(), target, tip, message)
    _git(repo, "update-ref", "-m", f"orc: {message}", f"refs/heads/{into}", commit, target)
    return MergeResult("merged", commit=commit)
