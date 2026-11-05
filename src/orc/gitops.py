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
import shlex
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

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
    return not is_remote(repo) and bool(URL_RE.match(repo))


# --- ssh remotes: the player's repo lives on another machine, and git runs there ---
#
# Same rules as a local repo, one place: this module. Code reaches a box only as a bundle that
# `git bundle create -` streams back over ssh; branches go out as a bundle streamed into a private
# scratch file on the remote and fetched into `refs/heads/orc/...`. The in-app merge is the same
# plumbing (merge-tree, commit-tree, update-ref old->new) and the same `git worktree list`
# check, executed on the remote: if the remote cannot be asked, nothing moves.
# Every remote command is an argv list quoted with shlex for the remote shell, and every
# string that reaches ssh or git was validated first (see parse_remote, check_branch, _rev).

SSH_MISSING = "ssh isn't installed (or isn't on PATH), so orc can't reach a remote war target."
_USER = r"(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,63}@)?"
_HOST = r"[A-Za-z0-9][A-Za-z0-9.-]{0,251}[A-Za-z0-9]"
_PATH = r"[A-Za-z0-9._/+@%=,][A-Za-z0-9._/+@%=,-]{0,1023}"  # no leading '-' or '~', no spaces, quotes, or shell syntax
REMOTE_RE = re.compile(rf"^(?P<host>{_USER}{_HOST}):(?P<path>{_PATH})$")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")  # a file name: no slash, so no leaving ~/.ssh
BRANCH_RE = re.compile(r"^orc(?:/[A-Za-z0-9_][A-Za-z0-9._-]*)+$")  # always under refs/heads/orc/
SSH_TIMEOUT_S = 60  # per command
SSH_TRANSFER_TIMEOUT_S = 1800  # bundles in or out
BUNDLE_MAGIC = ("# v2 git bundle", "# v3 git bundle")


@dataclass(frozen=True)
class Remote:
    """`user@host:path`, an optional port, and the *name* of a key in ~/.ssh (never its contents)."""

    target: str
    port: int | None = None
    key: str | None = None

    @property
    def host(self) -> str:
        return self.target.split(":", 1)[0]

    @property
    def path(self) -> str:
        return self.target.split(":", 1)[1]

    def to_dict(self) -> dict[str, object]:
        return {"target": self.target, "port": self.port, "key": self.key}

    def ssh_command(self, inner: str) -> str:
        """An `ssh` command line for the player to paste, running `inner` on the remote."""
        words = ["ssh"]
        if self.port:
            words += ["-p", str(self.port)]
        if self.key:  # the key name matches KEY_RE, so it needs no quoting, and `~` must stay unquoted to expand
            words += ["-i", f"~/.ssh/{self.key}" if KEY_RE.match(self.key) else shlex.quote(self.key)]
        words += ["-t", shlex.quote(self.host), shlex.quote(inner)]
        return " ".join(words)


class Transport(Protocol):
    """Runs one command on the remote machine. `stdin` / `stdout` are local files to stream."""

    def run(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        stdin: Path | None = None,
        stdout: Path | None = None,
        max_bytes: int | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


def remote_command(argv: list[str], env: dict[str, str] | None = None) -> str:
    """The one string ssh hands the remote shell: every word quoted, so nothing is interpreted there."""
    words = list(argv)
    if env:
        if not all(re.fullmatch(r"[A-Z][A-Z0-9_]*", k) for k in env):
            raise ValueError("bad environment variable name")
        words = ["env", *(f"{k}={v}" for k, v in env.items()), *words]
    return shlex.join(words)


def spawn(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    stdin: Path | None = None,
    stdout: Path | None = None,
    max_bytes: int | None = None,
    timeout: float | None = None,
    missing: str = GIT_MISSING,
) -> subprocess.CompletedProcess[str]:
    """Run a process with files for stdin/stdout, a read cap on stdout and a hard time limit.

    With `max_bytes`, at most that many bytes (plus one, so the caller can tell) are read and the
    process is killed after. A timeout kills it and reports exit code 255, like an ssh failure.
    """
    expired = threading.Event()
    data = b""
    with open(stdin or os.devnull, "rb") as fin, tempfile.TemporaryFile() as err:
        fout = open(stdout, "wb") if stdout else None
        try:
            try:
                proc = subprocess.Popen(argv, stdin=fin, stdout=fout or subprocess.PIPE, stderr=err, env=env)
            except FileNotFoundError:
                raise RuntimeError(missing) from None

            def expire() -> None:
                expired.set()
                proc.kill()

            timer = threading.Timer(timeout, expire) if timeout else None
            if timer:
                timer.start()
            try:
                if proc.stdout:
                    data = proc.stdout.read(max_bytes + 1) if max_bytes else proc.stdout.read()
                    if max_bytes and len(data) > max_bytes:
                        proc.kill()
                code = proc.wait()
            finally:
                if timer:
                    timer.cancel()
                if proc.stdout:
                    proc.stdout.close()
        finally:
            if fout:
                fout.close()
        err.seek(0)
        stderr = err.read(20_000).decode(errors="replace")
    if expired.is_set():
        code, stderr = 255, f"timed out after {timeout:g}s. {stderr}".strip()
    return subprocess.CompletedProcess(argv, code, data.decode(errors="replace"), stderr)


def key_path(name: str) -> Path:
    if not KEY_RE.match(name):
        raise ValueError("The ssh key is the name of a file in ~/.ssh, nothing more.")
    return paths.ssh_dir() / name


def ssh_argv(remote: Remote, command: str) -> list[str]:
    """The full local ssh invocation. Fixed options first, then validated pieces, then `--` and the command."""
    argv = [
        "ssh", "-T", "-a", "-x",  # no tty, no agent forwarding, no X11
        "-o", "BatchMode=yes",  # never prompt: a password or passphrase prompt would hang the engine
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=yes",  # unknown or changed host keys are refused, whatever ~/.ssh/config says
        "-o", "ForwardAgent=no",
        "-o", "ClearAllForwardings=yes",
        "-o", "PermitLocalCommand=no",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=4",
    ]
    if remote.port:
        argv += ["-p", str(remote.port)]
    if remote.key:
        argv += ["-o", "IdentitiesOnly=yes", "-i", str(key_path(remote.key))]  # the key's *path* only; ssh reads the file
    return [*argv, "--", remote.host, command]


class SshTransport:
    def __init__(self, remote: Remote) -> None:
        self.remote = remote

    def run(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        stdin: Path | None = None,
        stdout: Path | None = None,
        max_bytes: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        timeout = SSH_TRANSFER_TIMEOUT_S if (stdin or stdout) else SSH_TIMEOUT_S
        full = ssh_argv(self.remote, remote_command(argv, env))
        return spawn(full, stdin=stdin, stdout=stdout, max_bytes=max_bytes, timeout=timeout, missing=SSH_MISSING)


_REMOTES: dict[str, tuple[Remote, Transport]] = {}
make_transport = SshTransport  # tests swap this for a local fake


def parse_remote(target: str, port: object = None, key: object = None) -> Remote:
    """Validate what the player typed, or raise ValueError. Nothing is run and no key is read."""
    target = target.strip()
    if len(target) > 1400 or not REMOTE_RE.match(target) or ":/" + "/" in target:  # no ssh://-style URLs
        raise ValueError("An ssh target looks like user@host:path/to/repo (no spaces or shell characters).")
    if port is None or port == "" or isinstance(port, bool):
        number = None
    else:
        try:
            number = int(port)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            raise ValueError("The ssh port must be a number.") from None
        if not 0 < number < 65536:
            raise ValueError("The ssh port must be between 1 and 65535.")
    name = str(key).strip() if key not in (None, "") else None
    if name and not key_path(name).is_file():  # key_path rejects anything but a bare file name
        raise ValueError(f"There is no key named {name} in ~/.ssh.")
    return Remote(target, number, name)


def probe_remote(remote: Remote) -> Transport:
    """Ask the remote whether it has a repo with a commit. Returns the transport to register; raises ValueError."""
    transport = make_transport(remote)
    result = transport.run(["git", "-C", remote.path, "rev-parse", "HEAD"])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        if "Host key verification failed" in detail or "REMOTE HOST IDENTIFICATION" in detail:
            detail += " (connect once with ssh from a terminal to trust the host key, then try again)"
        raise ValueError(f"{remote.target} is unreachable, or not a git repo with at least one commit ({detail}).")
    return transport


def register_remote(remote: Remote, transport: Transport | None = None) -> None:
    """Make `remote.target` a repo reference every function below can use."""
    _REMOTES[remote.target] = (remote, transport or make_transport(remote))


def forget_remote(target: str) -> None:
    _REMOTES.pop(target, None)


def get_remote(repo: str) -> Remote | None:
    entry = _REMOTES.get(repo)
    return entry[0] if entry else None


def is_remote(repo: str) -> bool:
    return repo in _REMOTES


def looks_remote(repo: str) -> bool:
    """Shaped like an ssh target, whether or not it is the active one (a saved delivery's old target)."""
    return is_remote(repo) or bool(REMOTE_RE.match(repo))


def reachable(repo: str) -> bool:
    """Can git answer for this repo at all? Tells a remote that is down from a branch that is gone."""
    try:
        return _run(repo, "rev-parse", "--git-dir").returncode == 0
    except RuntimeError:
        return False


def check_branch(branch: str) -> str:
    """Only `orc/...` branches are ever fetched or merged, so a ref can't leave refs/heads/orc/."""
    if not BRANCH_RE.match(branch) or ".." in branch or branch.endswith((".", ".lock")) or "/." in branch or "//" in branch:
        raise RuntimeError(f"{branch!r} is not an orc branch name.")
    return branch


def _rev(rev: str) -> str:
    """A revision that can't be read as an option (`--output=...` would write a file)."""
    if not rev or rev.startswith("-") or any(c.isspace() or ord(c) < 32 for c in rev):
        raise RuntimeError(f"{rev!r} is not a usable revision.")
    return rev


def _run(
    repo: str, *args: str, env: dict[str, str] | None = None, max_bytes: int | None = None
) -> subprocess.CompletedProcess[str]:
    if repo in _REMOTES:
        remote, transport = _REMOTES[repo]
        return transport.run(["git", "-C", remote.path, *args], env=env, max_bytes=max_bytes)
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
    if is_remote(repo):
        try:
            _git(repo, "rev-parse", "HEAD")
        except RuntimeError as exc:
            raise ValueError(f"{repo} is unreachable, or not a git repo with at least one commit ({exc}).") from exc
        return repo
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
    if repo in _REMOTES:  # `-` makes git write the bundle to stdout, which ssh carries into `out`
        remote, transport = _REMOTES[repo]
        tmp = out.with_name(f"{out.name}.{secrets.token_hex(4)}.part")
        try:
            result = transport.run(["git", "-C", remote.path, "bundle", "create", "-", "HEAD"], stdout=tmp)
            if result.returncode != 0:
                raise RuntimeError(f"git bundle failed: {result.stderr.strip() or f'exit code {result.returncode}'}")
            with open(tmp, "rb") as f:  # a login banner on stdout would corrupt the bundle: insist on git's header
                first = f.readline(64).decode(errors="replace").strip()
            if first not in BUNDLE_MAGIC:
                raise RuntimeError("The remote did not send a git bundle (does its shell print something at login?).")
            os.replace(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)
    else:
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
    check_branch(branch)
    spec = f"refs/heads/{branch}:refs/heads/{branch}"  # no '+': an existing branch is never overwritten
    if repo not in _REMOTES:
        _git(repo, "fetch", "--quiet", str(bundle), spec)
        return
    # Remote: stream the bundle into a private scratch file there, fetch from it, remove it.
    _, transport = _REMOTES[repo]
    put = transport.run(
        ["sh", "-c", 'umask 077; f=$(mktemp "${TMPDIR:-/tmp}/orc-bundle.XXXXXX") && cat > "$f" && printf %s "$f"', "sh"],
        stdin=bundle,
    )
    scratch = put.stdout.strip()
    if put.returncode != 0 or not re.fullmatch(r"/[A-Za-z0-9._/+-]+", scratch):
        raise RuntimeError(f"Couldn't send the bundle to {repo}: {put.stderr.strip() or put.stdout.strip() or 'no scratch file'}")
    try:
        _git(repo, "fetch", "--quiet", scratch, spec)
    finally:
        transport.run(["rm", "-f", "--", scratch])


def branch_tip(repo: str, branch: str) -> str | None:
    """The commit `refs/heads/<branch>` points at, or None if there is no such branch."""
    result = _run(repo, "rev-parse", "-q", "--verify", f"refs/heads/{branch}^{{commit}}")
    return (result.stdout.strip() or None) if result.returncode == 0 else None


def has_commit(repo: str, sha: str) -> bool:
    return _run(repo, "cat-file", "-e", f"{_rev(sha)}^{{commit}}").returncode == 0


def is_ancestor(repo: str, older: str, newer: str) -> bool:
    return _run(repo, "merge-base", "--is-ancestor", _rev(older), _rev(newer)).returncode == 0


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
    spec = f"{_rev(base)}...{_rev(head)}"
    if repo in _REMOTES:  # same cap, enforced by killing ssh once enough has arrived
        result = _run(repo, "diff", "--no-color", "--no-ext-diff", "--no-textconv", spec, "--", max_bytes=limit)
        raw = result.stdout.encode()
        if len(raw) > limit:
            raw = raw[:limit]
            cut = raw.rfind(b"\n")
            return (raw[: cut + 1] if cut >= 0 else raw).decode(errors="replace"), True
        if result.returncode != 0:
            raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
        return result.stdout, False
    argv = ["git", "-C", repo, "diff", "--no-color", "--no-ext-diff", "--no-textconv", spec, "--"]
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
    fallback: dict[str, str] = {}
    if _run(repo, "var", "GIT_COMMITTER_IDENT").returncode != 0:
        for who in ("AUTHOR", "COMMITTER"):
            env.setdefault(f"GIT_{who}_NAME", "orc")
            env.setdefault(f"GIT_{who}_EMAIL", "orc@localhost")
            fallback[f"GIT_{who}_NAME"], fallback[f"GIT_{who}_EMAIL"] = "orc", "orc@localhost"
    if repo in _REMOTES:  # only the fallback identity travels; the local environment stays local
        result = _run(repo, "commit-tree", _rev(tree), "-p", _rev(target), "-p", _rev(tip), "-m", message, env=fallback)
    else:
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
    check_branch(branch)
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
