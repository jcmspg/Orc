"""SSH remotes: the same fetch / diff / merge rules, with git run "over ssh" through a local fake transport.

No ssh server is involved: `LocalTransport` runs the quoted remote command in a local `sh -c`.
The ssh command line itself is checked separately, never executed.
"""

import asyncio
import json
import shlex
import subprocess
from pathlib import Path

import pytest

from orc import gitops, paths
from orc.board import Board, Delivery
from orc.engine import Engine
from orc.persist import BoardStore
from tests.fakes import LocalTransport
from tests.test_engine import all_idle, isolated_git, make_engine, repo, until  # noqa: F401  (fixtures)
from tests.test_spoils import TaskRuntime, git


@pytest.fixture(autouse=True)
def clean_registry():
    gitops._REMOTES.clear()
    yield
    gitops._REMOTES.clear()


@pytest.fixture
def ssh_dir(tmp_path, monkeypatch) -> Path:
    """A scratch ~/.ssh with a key whose contents must never show up anywhere."""
    d = tmp_path / "dot-ssh"
    d.mkdir()
    (d / "id_work").write_text("-----BEGIN PRIVATE KEY-----\nTOP-SECRET-KEY-MATERIAL\n")
    monkeypatch.setattr(paths, "ssh_dir", lambda: d)
    return d


@pytest.fixture
def transport(monkeypatch) -> LocalTransport:
    t = LocalTransport()
    monkeypatch.setattr(gitops, "make_transport", lambda remote: t)
    return t


@pytest.fixture
def remote(repo, transport):
    r = gitops.Remote(f"orc@fakehost:{repo}", 2222, None)
    gitops.register_remote(r, transport)
    return r, transport


def make_delivery(clone: Path, repo: Path, tmp_path: Path, name: str = "orc/durg/x-1") -> tuple[Path, str]:
    """A delivered branch the way a box makes one: a commit in a clone, shipped as a bundle."""
    bundle = tmp_path / "b.bundle"
    if not clone.exists():
        subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    git(clone, "switch", "-q", "-c", name)
    (clone / "new.txt").write_text("hi\n")
    git(clone, "add", "-A")
    git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "work")
    git(clone, "bundle", "create", str(bundle), name)
    return bundle, git(clone, "rev-parse", "HEAD")


# --- validation: nothing odd reaches ssh or a remote shell ---


def test_parse_remote_accepts_the_normal_forms(ssh_dir):
    r = gitops.parse_remote("me@host.example:/srv/repo", "2200", "id_work")
    assert (r.host, r.path, r.port, r.key) == ("me@host.example", "/srv/repo", 2200, "id_work")
    assert gitops.parse_remote("host:repos/a.git").port is None
    assert gitops.parse_remote("me@h1:/srv/a_b-c.d", 22, "").key is None


@pytest.mark.parametrize(
    "bad",
    [
        "-oProxyCommand=touch /tmp/x@h:/p",  # option smuggled in as the user
        "-oProxyCommand=x:/p",
        "-p@host:/p",
        "me@-host:/p",  # host that ssh would read as an option
        "me@host:-oFoo",  # path read as an option by a tool on the remote
        "me@host:/p;rm -rf ~",
        "me@host:/p && id",
        "me@host:/p$(id)",
        "me@host:/`id`",
        "me@host:/p|cat",
        "me@host:/p\nid",
        "me@host:/a b",
        "me@host:'/p'",
        'me@host:"/p"',
        "me@host:~/repo",
        "me@host;id:/p",
        "me@ho st:/p",
        "me@host:",
        "nohost",
        "/just/a/path",
        "ssh://me@host/p",
        "me@host:/p\x00",
        "",
    ],
)
def test_parse_remote_rejects_anything_a_shell_or_ssh_could_read(bad, ssh_dir):
    with pytest.raises(ValueError):
        gitops.parse_remote(bad)


@pytest.mark.parametrize("port", ["0", "65536", "-1", "abc", "22; id", "2.5", True, [22]])
def test_parse_remote_rejects_bad_ports(port):
    with pytest.raises(ValueError):
        gitops.parse_remote("me@h:/r", port)


@pytest.mark.parametrize(
    "key",
    ["../../etc/passwd", "../id_work", "/etc/passwd", "~/x", "-F", "-oProxyCommand=x", "a b", "id_work/", ".hidden", "a\nb", "id_missing"],
)
def test_parse_remote_rejects_keys_that_are_not_a_name_in_the_ssh_dir(key, ssh_dir):
    with pytest.raises(ValueError):
        gitops.parse_remote("me@h:/r", None, key)


def test_a_key_that_is_a_directory_is_not_a_key(ssh_dir):
    (ssh_dir / "subdir").mkdir()
    with pytest.raises(ValueError):
        gitops.parse_remote("me@h:/r", None, "subdir")


@pytest.mark.parametrize(
    "name",
    ["main", "orc", "orc/", "orc/../main", "orc/a/../../b", "orc//a", "orc/.hidden", "orc/a.lock", "orc/a.", "-x",
     "refs/heads/orc/a", "orc/a b", "orc/a;id", "orc/$(id)", "orc/a\nb", "orc/-x"],
)
def test_only_orc_branch_names_are_ever_fetched_or_merged(name, repo, tmp_path):
    with pytest.raises(RuntimeError):
        gitops.fetch_branch(str(repo), tmp_path / "none.bundle", name)
    with pytest.raises(RuntimeError):
        gitops.merge(str(repo), name, "main")
    assert git(repo, "branch", "--list") == "* main"


@pytest.mark.parametrize("rev", ["--output=ORC-PWNED", "-x", "a b", ""])
def test_revisions_cannot_be_read_as_options(rev, repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError):
        gitops.diff(str(repo), rev, "main")
    with pytest.raises(RuntimeError):
        gitops.diff(str(repo), "main", rev)
    with pytest.raises(RuntimeError):
        gitops.has_commit(str(repo), rev)
    assert not (tmp_path / "ORC-PWNED").exists() and not (repo / "ORC-PWNED").exists()


# --- the ssh command line ---


def test_ssh_argv_is_hardened_names_the_key_and_quotes_the_command(ssh_dir):
    r = gitops.Remote("me@h:/srv/repo", 22, "id_work")
    command = gitops.remote_command(["git", "-C", "/a b", "status"], {"GIT_AUTHOR_NAME": "o rc"})
    argv = gitops.ssh_argv(r, command)
    assert argv[0] == "ssh"
    assert argv[-3:] == ["--", "me@h", command]  # "--" before the host: nothing after it is an option
    opts = [argv[i + 1] for i, a in enumerate(argv) if a == "-o"]
    assert {"BatchMode=yes", "StrictHostKeyChecking=yes", "ForwardAgent=no", "ClearAllForwardings=yes", "IdentitiesOnly=yes"} <= set(opts)
    assert any(o.startswith("ConnectTimeout=") for o in opts)
    assert not any("StrictHostKeyChecking=no" in o or "accept-new" in o or "UserKnownHostsFile" in o for o in opts)
    assert "-a" in argv and "-T" in argv  # no agent forwarding, no tty
    assert "-A" not in argv
    assert argv[argv.index("-i") + 1] == str(ssh_dir / "id_work")
    assert argv[argv.index("-p") + 1] == "22"
    assert shlex.split(command) == ["env", "GIT_AUTHOR_NAME=o rc", "git", "-C", "/a b", "status"]
    assert "TOP-SECRET" not in " ".join(argv)  # the key is a path, never contents


def test_ssh_transport_passes_the_argv_to_spawn_with_timeouts(monkeypatch, ssh_dir):
    seen = {}

    def fake(argv, **kw):
        seen.clear()
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(gitops, "spawn", fake)
    gitops.SshTransport(gitops.Remote("me@h:/r")).run(["git", "status"])
    assert seen["timeout"] == gitops.SSH_TIMEOUT_S and seen["argv"][0] == "ssh"
    gitops.SshTransport(gitops.Remote("me@h:/r")).run(["cat"], stdin=Path("/dev/null"))
    assert seen["timeout"] == gitops.SSH_TRANSFER_TIMEOUT_S
    assert seen["missing"] == gitops.SSH_MISSING


@pytest.mark.parametrize(
    "nasty",
    ["$(touch PWNED)", "`touch PWNED`", "a; touch PWNED", "a' ; touch PWNED ; '", 'a" ; touch PWNED', "a\ntouch PWNED", "a && touch PWNED", "*", "~", "-n", "$HOME"],
)
def test_remote_command_quoting_survives_a_real_shell(nasty, tmp_path):
    """Whatever a branch name or message says, the remote shell sees exactly one literal word."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    done = subprocess.run(
        ["sh", "-c", gitops.remote_command(["printf", "%s", nasty])], cwd=cwd, capture_output=True, text=True
    )
    assert done.stdout == nasty
    assert list(cwd.iterdir()) == []


def test_remote_env_names_are_restricted():
    with pytest.raises(ValueError):
        gitops.remote_command(["git"], {"X; id": "1"})


def test_spawn_caps_output_and_enforces_the_timeout():
    capped = gitops.spawn(["yes"], max_bytes=100, timeout=10)
    assert len(capped.stdout) == 101
    slow = gitops.spawn(["sleep", "5"], timeout=0.2)
    assert slow.returncode == 255 and "timed out" in slow.stderr
    with pytest.raises(RuntimeError, match="ssh"):
        gitops.spawn(["definitely-not-a-binary-orc"], missing="ssh isn't installed")


# --- behaviour over the transport ---


def test_remote_is_not_a_url(remote):
    r, _ = remote
    assert gitops.is_remote(r.target) and not gitops.is_url(r.target)
    assert gitops.is_url("git@github.com:a/b.git")  # an unregistered lookalike stays a plain URL
    assert gitops.validate_repo(r.target) == r.target


def test_unreachable_remote_is_rejected(remote):
    r, transport = remote
    transport.down = True
    with pytest.raises(ValueError, match="unreachable"):
        gitops.validate_repo(r.target)
    assert not gitops.reachable(r.target)


def test_head_bundle_comes_back_over_the_transport_and_clones(repo, remote, tmp_path):
    r, transport = remote
    bundle, branch = gitops.head_bundle(r.target)
    assert branch == "main"
    clone = tmp_path / "c"
    subprocess.run(["git", "clone", "-q", str(bundle), str(clone)], check=True)
    assert git(clone, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
    assert transport.ran("bundle")


def test_a_login_banner_in_the_bundle_stream_is_refused(repo, remote, tmp_path):
    r, transport = remote
    real = transport.run

    def noisy(argv, **kw):
        if "bundle" in argv and kw.get("stdout"):
            kw["stdout"].write_bytes(b"Welcome to fakehost!\n")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real(argv, **kw)

    transport.run = noisy  # type: ignore[method-assign]
    out = tmp_path / "out.bundle"
    with pytest.raises(RuntimeError, match="did not send a git bundle"):
        gitops.bundle_head(r.target, out)
    assert not out.exists() and not list(tmp_path.glob("*.part"))


def test_fetch_then_merge_run_git_on_the_remote_only(repo, remote, tmp_path):
    r, transport = remote
    git(repo, "branch", "develop")
    git(repo, "switch", "-q", "-c", "scratch")  # HEAD is on scratch; main and develop are free
    bundle, tip = make_delivery(tmp_path / "clone", repo, tmp_path)

    gitops.fetch_branch(r.target, bundle, "orc/durg/x-1")
    assert git(repo, "rev-parse", "orc/durg/x-1") == tip
    assert transport.calls[-1][:3] == ["rm", "-f", "--"]  # the scratch bundle is removed
    scratch = transport.calls[-1][3]
    assert not Path(scratch).exists() and scratch.startswith("/")

    develop_before = git(repo, "rev-parse", "develop")
    transport.calls.clear()
    result = gitops.merge(r.target, "orc/durg/x-1", "develop", expect=tip)
    assert result.status == "merged"
    assert git(repo, "rev-parse", "develop^2") == tip
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "scratch"
    assert not (repo / "new.txt").exists()  # no working tree was touched
    # Every step, including the worktree check and the compare-and-swap, was asked of the remote.
    words = [c[3] if c[:2] == ["git", "-C"] else c[0] for c in transport.calls]
    assert words.index("worktree") < words.index("update-ref")
    update = transport.ran("update-ref")[0]
    assert update[-3:] == ["refs/heads/develop", result.commit, develop_before]  # ref, new, expected old
    for forbidden in ("checkout", "switch", "reset", "restore", "merge", "read-tree", "add", "stash", "clean", "rebase", "cherry-pick"):
        assert not transport.ran(forbidden)


def test_remote_scratch_file_is_private(repo, remote, tmp_path):
    r, transport = remote
    bundle, _ = make_delivery(tmp_path / "clone", repo, tmp_path)
    seen = {}
    real = transport.run

    def spy(argv, **kw):
        out = real(argv, **kw)
        if argv[:2] == ["sh", "-c"]:
            seen["path"] = out.stdout
            seen["mode"] = Path(out.stdout).stat().st_mode & 0o777
        return out

    transport.run = spy  # type: ignore[method-assign]
    gitops.fetch_branch(r.target, bundle, "orc/durg/x-1")
    assert seen["mode"] == 0o600 and "orc-bundle." in seen["path"]


def test_a_failed_remote_fetch_still_cleans_up_and_never_overwrites(repo, remote, tmp_path):
    r, transport = remote
    bundle, _ = make_delivery(tmp_path / "clone", repo, tmp_path)
    other = git(repo, "commit-tree", "-p", "main", "-m", "other", "main^{tree}")
    git(repo, "branch", "orc/durg/x-1", other)  # a diverged branch of that name already exists
    with pytest.raises(RuntimeError):
        gitops.fetch_branch(r.target, bundle, "orc/durg/x-1")
    assert git(repo, "rev-parse", "orc/durg/x-1") == other
    assert transport.calls[-1][:3] == ["rm", "-f", "--"]


def test_merge_refuses_a_branch_checked_out_on_the_remote(repo, remote, tmp_path):
    r, transport = remote
    sha = git(repo, "rev-parse", "main")
    bundle, _ = make_delivery(tmp_path / "clone", repo, tmp_path)
    gitops.fetch_branch(r.target, bundle, "orc/durg/x-1")
    transport.calls.clear()
    result = gitops.merge(r.target, "orc/durg/x-1", "main")  # main is checked out in `repo`
    assert result.status == "checkedOut" and result.worktrees == [str(repo)]
    assert git(repo, "rev-parse", "main") == sha
    assert transport.ran("worktree") and not transport.ran("update-ref")


def test_merge_moves_nothing_if_the_remote_cannot_be_asked_about_worktrees(repo, remote, tmp_path):
    r, transport = remote
    git(repo, "branch", "develop")
    git(repo, "switch", "-q", "-c", "scratch")
    bundle, _ = make_delivery(tmp_path / "clone", repo, tmp_path)
    gitops.fetch_branch(r.target, bundle, "orc/durg/x-1")
    develop = git(repo, "rev-parse", "develop")
    transport.fail_on = {"worktree"}
    with pytest.raises(RuntimeError):
        gitops.merge(r.target, "orc/durg/x-1", "develop")
    assert git(repo, "rev-parse", "develop") == develop and not transport.ran("update-ref")


def test_merge_over_a_dropped_connection_at_the_last_step_moves_nothing(repo, remote, tmp_path):
    r, transport = remote
    git(repo, "branch", "develop")
    git(repo, "switch", "-q", "-c", "scratch")
    bundle, _ = make_delivery(tmp_path / "clone", repo, tmp_path)
    gitops.fetch_branch(r.target, bundle, "orc/durg/x-1")
    develop = git(repo, "rev-parse", "develop")
    transport.fail_on = {"update-ref"}
    with pytest.raises(RuntimeError):
        gitops.merge(r.target, "orc/durg/x-1", "develop")
    assert git(repo, "rev-parse", "develop") == develop


def test_a_concurrent_move_on_the_remote_loses_to_the_compare_and_swap(repo, remote, tmp_path):
    r, transport = remote
    git(repo, "branch", "develop")
    git(repo, "switch", "-q", "-c", "scratch")
    bundle, _ = make_delivery(tmp_path / "clone", repo, tmp_path)
    gitops.fetch_branch(r.target, bundle, "orc/durg/x-1")
    real = transport.run

    def racing(argv, **kw):
        if "update-ref" in argv:  # someone commits to develop between our read and our write
            other = git(repo, "commit-tree", "-p", "develop", "-m", "raced", "develop^{tree}")
            git(repo, "update-ref", "refs/heads/develop", other)
        return real(argv, **kw)

    transport.run = racing  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="update-ref"):
        gitops.merge(r.target, "orc/durg/x-1", "develop")
    assert git(repo, "log", "-1", "--format=%s", "develop") == "raced"


def test_diff_over_the_transport_and_its_cap(repo, remote):
    r, _ = remote
    base = git(repo, "rev-parse", "main")
    git(repo, "branch", "orc/d")
    assert gitops.diff(r.target, base, "orc/d") == ("", False)
    (repo / "big.txt").write_text("x" * 50 + "\n" + "y" * 5000)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "big")
    text, cut = gitops.diff(r.target, base, "main", limit=200)
    assert cut and len(text.encode()) <= 200 and text.endswith("\n")


# --- the engine ---


def test_set_remote_delivers_and_hands_over_the_command_while_main_is_checked_out(repo, ssh_dir, transport):
    target = f"orc@fakehost:{repo}"

    async def scenario():
        events: list = []
        engine = make_engine(events, TaskRuntime(delay=0))
        engine.boot()
        await engine.handle({"op": "setRemote", "target": target, "port": 2200, "key": "id_work"})
        assert engine.board.repo == target
        assert engine.board.remote == {"target": target, "port": 2200, "key": "id_work"}
        await engine.handle({"op": "setAutonomy", "id": "orc-1", "on": True})
        await engine.handle({"op": "command", "ids": ["orc-1"], "text": "Add a login page"})
        await until(lambda: engine.board.gold == 2 and all_idle(engine))
        d = next(iter(engine.board.deliveries.values()))
        assert d.status == "delivered" and d.repo == target and d.base_branch == "main"
        assert git(repo, "rev-parse", d.branch) == d.head

        # main is checked out in the remote repo: orc moves nothing and hands over a command to run over ssh
        await engine.handle({"op": "merge", "id": d.id})
        got = engine.board.deliveries[d.id]
        assert got.status in ("delivered", "conflict") and git(repo, "rev-parse", "main") == d.base
        assert got.command and got.command.startswith("ssh -p 2200 -i ~/.ssh/id_work -t orc@fakehost ")
        assert "git merge --no-ff" in got.command and str(repo) in got.command
        assert "checked out" in (got.note or "")

        git(repo, "switch", "-q", "-c", "elsewhere")  # now main is free to move
        await engine.handle({"op": "merge", "id": d.id})
        assert engine.board.deliveries[d.id].status in ("merged", "conflict")
        snap = engine.board.snapshot()
        assert {"type": "board.remote", "remote": engine.board.remote} in snap
        await engine.shutdown()
        return events

    events = asyncio.run(scenario())
    assert any(e["type"] == "board.remote" and e["remote"]["key"] == "id_work" for e in events)
    assert "TOP-SECRET" not in json.dumps(events)  # key material is never read, let alone evented
    assert not transport.ran("checkout") and not transport.ran("reset")


def test_set_remote_with_a_bad_target_runs_nothing_and_changes_nothing(ssh_dir, monkeypatch):
    created = []
    monkeypatch.setattr(gitops, "make_transport", lambda r: created.append(r))  # would be a bug if used

    async def scenario():
        events: list = []
        engine = make_engine(events)
        engine.boot()
        ops = [
            {"op": "setRemote", "target": "-oProxyCommand=id@h:/p"},
            {"op": "setRemote", "target": "me@h:/p;id"},
            {"op": "setRemote", "target": "me@h:/p", "port": "x"},
            {"op": "setRemote", "target": "me@h:/p", "key": "../../etc/passwd"},
            {"op": "setRemote", "target": "me@h:/p", "key": "id_absent"},
            {"op": "setRemote", "target": 5},
            {"op": "setRemote"},
        ]
        for op in ops:
            await engine.handle(op)
        assert engine.board.repo is None and engine.board.remote is None
        assert not any(e["type"] in ("board.remote", "board.repo") for e in events)
        assert sum(1 for e in events if e["type"] == "orc.log" and "Bad war target" in e["line"]) == len(ops)
        await engine.shutdown()

    asyncio.run(scenario())
    assert created == []


def test_set_remote_that_does_not_answer_is_not_registered(repo, ssh_dir, transport):
    transport.down = True

    async def scenario():
        engine = make_engine([])
        engine.boot()
        await engine.handle({"op": "setRemote", "target": f"orc@fakehost:{repo}"})
        assert engine.board.remote is None and engine.board.repo is None
        await engine.shutdown()

    asyncio.run(scenario())
    assert gitops._REMOTES == {}


def test_a_failed_new_target_keeps_the_working_one(repo, ssh_dir, transport):
    target = f"orc@fakehost:{repo}"

    async def scenario():
        engine = make_engine([])
        engine.boot()
        await engine.handle({"op": "setRemote", "target": target})
        transport.down = True
        await engine.handle({"op": "setRemote", "target": target, "port": 2222})
        assert engine.board.remote == {"target": target, "port": None, "key": None}
        assert gitops.is_remote(target)
        await engine.shutdown()

    asyncio.run(scenario())


def test_setting_a_local_repo_clears_the_remote(repo, remote):
    r, _ = remote

    async def scenario():
        events: list = []
        engine = make_engine(events)
        engine.boot()
        engine.board.apply({"type": "board.remote", "remote": r.to_dict()})
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        assert engine.board.remote is None and engine.board.repo == str(repo)
        assert {"type": "board.remote", "remote": None} in events
        await engine.handle({"op": "setRepo", "repo": r.target})  # an ssh target is not a local path
        assert engine.board.repo == str(repo)
        await engine.shutdown()

    asyncio.run(scenario())


def test_the_remote_is_saved_and_restored_without_touching_the_network(repo, ssh_dir, transport, tmp_path):
    store = BoardStore(tmp_path / "board.json")
    target = f"orc@fakehost:{repo}"

    async def first():
        engine = make_engine([])
        engine.store = store
        engine.boot()
        await engine.handle({"op": "setRemote", "target": target, "key": "id_work"})
        await engine.shutdown()

    asyncio.run(first())
    saved = json.loads(store.path.read_text())
    assert saved["remote"] == {"target": target, "port": None, "key": "id_work"}
    assert "TOP-SECRET" not in store.path.read_text()

    gitops._REMOTES.clear()
    transport.calls.clear()

    async def second():
        events: list = []
        engine = make_engine(events)
        engine.store = store
        engine.boot()
        assert engine.board.remote == {"target": target, "port": None, "key": "id_work"}
        assert engine.board.repo == target and gitops.is_remote(target)
        assert [e["type"] for e in events if e["type"].startswith("board.")] == ["board.repo", "board.remote"]
        await engine.shutdown()

    asyncio.run(second())
    assert transport.calls == []  # boot did not wait on ssh


def test_a_saved_remote_with_a_vanished_key_is_dropped(repo, ssh_dir, transport, tmp_path):
    store = BoardStore(tmp_path / "board.json")
    target = f"orc@fakehost:{repo}"
    board = Board()
    board.apply({"type": "board.repo", "repo": target})
    board.apply({"type": "board.remote", "remote": {"target": target, "port": None, "key": "id_gone"}})
    store.save(board)

    async def scenario():
        engine = make_engine([])
        engine.store = store
        engine.boot()
        assert engine.board.remote is None
        await engine.shutdown()

    asyncio.run(scenario())


def test_down_ssh_does_not_mark_a_delivery_missing(remote):
    r, transport = remote
    d = Delivery("d1", "orc/a", "t", "o", r.target, "b", "main", "h", "s")
    transport.down = True
    assert Engine._look(d).change is None


def test_a_delivery_into_an_old_remote_is_left_alone_and_the_player_is_told():
    d = Delivery("d1", "orc/a", "t", "o", "me@oldhost:/srv/x", "b" * 40, "main", "h" * 40, "s")
    assert Engine._look(d).change is None  # not registered: not "missing", not anything
    with pytest.raises(RuntimeError, match="not the war target"):
        Engine._need_target(d)
    assert Engine._where(d, "git merge --no-ff orc/a") == "ssh -t me@oldhost 'cd /srv/x && git merge --no-ff orc/a'"
