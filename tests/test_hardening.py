"""Agent timeouts, bad ops, and bundle housekeeping."""

import asyncio
import os
import time

from orc import gitops
from orc.engine import Engine
from orc.persist import BoardStore
from orc.runtimes import DummyRuntime
from tests.fakes import LocalSandbox
from tests.test_engine import all_idle, branches, make_engine, repo, until, isolated_git  # noqa: F401


def run(coro):
    return asyncio.run(coro)


def bundle_files() -> list[str]:
    return sorted(p.name for p in gitops.bundles_dir().iterdir())


# --- agent timeout ---

class Hangs(DummyRuntime):
    """Hangs on the first code run, then behaves."""

    def __init__(self):
        super().__init__(delay=0)
        self.calls = 0

    async def run(self, box, prompt, on_log, *, kind, hints):
        if kind == "code":
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(3600)
        return await super().run(box, prompt, on_log, kind=kind, hints={**hints, "attempt": 1})


def test_a_hung_agent_times_out_into_retry_or_abandon(repo):
    async def scenario():
        events: list = []
        runtime = Hangs()
        engine = make_engine(events, runtime)
        engine.agent_timeout_s = 0.1
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "command", "ids": ["orc-2"], "text": "Add a page"})
        await until(lambda: engine.board.orcs["orc-2"].state == "failed")
        log = [line["text"] for line in engine.board.orcs["orc-2"].log]
        box = LocalSandbox.created[-1]
        killed = box.killed
        await engine.handle({"op": "retry", "id": "orc-2"})
        await until(lambda: runtime.calls == 2 and engine.board.orcs["orc-2"].state != "failed")
        await engine.shutdown()
        return log, killed

    log, killed = run(scenario())
    assert any("still working after" in line for line in log)
    assert killed == 1  # the stuck agent was stopped before the player was asked


def test_a_timeout_that_is_not_ours_passes_through(repo):
    class Slow(DummyRuntime):
        async def run(self, box, prompt, on_log, *, kind, hints):
            raise TimeoutError("the model API timed out")

    async def scenario():
        engine = make_engine([], Slow(delay=0))
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "command", "ids": ["orc-2"], "text": "Fix"})
        await until(lambda: engine.board.orcs["orc-2"].state == "failed")
        log = [line["text"] for line in engine.board.orcs["orc-2"].log]
        killed = LocalSandbox.created[-1].killed
        await engine.handle({"op": "abandon", "id": "orc-2"})
        await engine.shutdown()
        return log, killed

    log, killed = run(scenario())
    assert any("the model API timed out" in line for line in log)
    assert killed == 0


# --- bad ops ---

def test_bad_ops_are_reported_not_raised():
    async def scenario():
        engine = make_engine([])
        engine.boot()
        for op in (
            {"op": "command", "ids": 5, "text": "x"},  # ids isn't a list
            {"op": "setAutonomy", "id": "orc-2", "on": object()},
            ["not", "a", "dict"],
            None,
        ):
            await engine.handle(op)  # must not raise
        hall = engine.board.orcs["orc-1"]
        await engine.shutdown()
        return [line["text"] for line in hall.log if line["level"] == "error"]

    errors = run(scenario())
    assert any("command op failed unexpectedly" in e for e in errors)


def test_failed_merge_answers_the_waiting_app():
    async def scenario():
        events: list = []
        engine = make_engine(events)
        engine.boot()

        async def boom(did):
            raise RuntimeError("disk on fire")

        engine.merge = boom
        from orc.board import Delivery
        d = Delivery(id="d1", branch="orc/x", task="t", owner="orc-2", repo="/nope", base="a", base_branch="main", head="b", stat="")
        engine._emit("branch.delivered", delivery=d.to_dict())
        await engine.handle({"op": "merge", "id": "d1"})
        await engine.shutdown()
        return engine.board.deliveries["d1"]

    d = run(scenario())
    assert "disk on fire" in (d.note or "")


# --- bundles ---

def test_head_bundle_is_shared_until_head_moves(repo):
    first, branch = gitops.head_bundle(str(repo))
    again, _ = gitops.head_bundle(str(repo))
    assert first == again and branch == "main"
    mtime = first.stat().st_mtime
    assert gitops.head_bundle(str(repo))[0].stat().st_mtime == mtime  # reused, not rebuilt

    (repo / "new.txt").write_text("x\n")
    os.system(f"git -C {repo} add . && git -C {repo} -c user.name=t -c user.email=t@t commit -qm two")
    moved, _ = gitops.head_bundle(str(repo))
    assert moved != first


def test_head_bundle_is_rebuilt_once_stale(repo):
    path, _ = gitops.head_bundle(str(repo))
    old = time.time() - gitops.HEAD_BUNDLE_REUSE_S - 5
    os.utime(path, (old, old))
    gitops.head_bundle(str(repo))
    assert path.stat().st_mtime > old + 1


def test_prune_keeps_referenced_and_recent_bundles():
    d = gitops.bundles_dir()
    old = time.time() - gitops.BUNDLE_MAX_AGE_S - 60
    for name in ("stale.bundle", "kept.bundle", "fresh.bundle"):
        (d / name).write_text("x")
    for name in ("stale.bundle", "kept.bundle"):
        os.utime(d / name, (old, old))
    assert gitops.prune_bundles([d / "kept.bundle"]) == 1
    assert bundle_files() == ["fresh.bundle", "kept.bundle"]


def test_delivered_branch_leaves_no_bundle_behind(repo):
    async def scenario():
        engine = make_engine([])
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "setAutonomy", "id": "orc-1", "on": True})
        await engine.handle({"op": "command", "ids": ["orc-1"], "text": "Add a login page"})
        await until(lambda: engine.board.gold == 2 and all_idle(engine))
        await engine.shutdown()
        return engine

    engine = run(scenario())
    assert len(branches(repo)) == 2
    assert all(d.bundle is None for d in engine.board.deliveries.values())
    assert [f for f in bundle_files() if not f.startswith("head-")] == []


def test_abandoned_work_drops_its_bundle(repo):
    class FailsAfterExport(DummyRuntime):
        async def run(self, box, prompt, on_log, *, kind, hints):
            if kind == "review":
                raise RuntimeError("reviewer quit")
            return await super().run(box, prompt, on_log, kind=kind, hints=hints)

    async def scenario():
        engine = make_engine([], FailsAfterExport(delay=0))
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "setAutonomy", "id": "orc-4", "on": True})
        await engine.handle({"op": "command", "ids": ["orc-2"], "text": "Fix"})
        await until(lambda: engine.board.orcs["orc-4"].state == "failed")
        assert any(not f.startswith("head-") for f in bundle_files())  # the exported work is waiting for review
        # Abandoning the reviewer leaves the work; shutting down must clear it.
        await engine.shutdown()

    run(scenario())
    assert [f for f in bundle_files() if not f.startswith("head-")] == []


def test_boot_sweeps_orphans_but_keeps_a_bundled_delivery(tmp_path, repo):
    from orc.board import Delivery

    d = gitops.bundles_dir()
    old = time.time() - gitops.BUNDLE_MAX_AGE_S - 60
    keep, orphan = d / "keep.bundle", d / "orphan.bundle"

    store = BoardStore(tmp_path / "board.json")

    async def scenario():
        seed = make_engine([])
        seed.store = store
        seed.boot()
        for f in (keep, orphan):  # made after the first boot's sweep, as a previous run would have left them
            f.write_text("x")
            os.utime(f, (old, old))
        seed._emit("branch.delivered", delivery=Delivery(
            id="k", branch="orc/k", task="t", owner="orc-2", repo="https://x/y.git", base="a", base_branch="main",
            head="b", stat="", status="bundled", bundle=str(keep)).to_dict())
        await seed.shutdown()  # saves

        engine = make_engine([])
        engine.store = store
        engine.boot()
        await engine.shutdown()

    run(scenario())
    assert bundle_files() == ["keep.bundle"]


# --- first-use image build ---

def test_first_use_image_build_is_announced(monkeypatch):
    from orc import sandbox

    built, notices = [], []
    state = {"exists": False}

    async def exists(image=sandbox.IMAGE):
        return state["exists"]

    async def build(image=sandbox.IMAGE, on_line=None):
        built.append(image)
        state["exists"] = True

    monkeypatch.setattr(sandbox, "image_exists", exists)
    monkeypatch.setattr(sandbox, "build_image", build)
    monkeypatch.setattr(sandbox, "image_notice", lambda text, level="info": notices.append((level, text)))

    async def twice():
        await asyncio.gather(sandbox.ensure_image(), sandbox.ensure_image())  # two jobs start together

    run(twice())
    assert built == [sandbox.IMAGE]  # built once
    assert [lvl for lvl, _ in notices] == ["warn", "ok"]


def test_failed_image_build_is_reported(monkeypatch):
    import pytest

    from orc import sandbox

    async def exists(image=sandbox.IMAGE):
        return False

    async def build(image=sandbox.IMAGE, on_line=None):
        raise sandbox.SandboxError("docker build failed: no network")

    notices = []
    monkeypatch.setattr(sandbox, "image_exists", exists)
    monkeypatch.setattr(sandbox, "build_image", build)
    monkeypatch.setattr(sandbox, "image_notice", lambda text, level="info": notices.append(level))
    with pytest.raises(sandbox.SandboxError):
        run(sandbox.ensure_image())
    assert notices == ["warn", "error"]
