import asyncio
import subprocess
from pathlib import Path

import pytest

from orc.engine import Engine
from orc.runtimes import DummyRuntime
from tests.fakes import LocalSandbox


@pytest.fixture(autouse=True)
def isolated_git(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for key, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }.items():
        monkeypatch.setenv(key, value)
    LocalSandbox.created.clear()


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "target"
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)
    return path


def make_engine(events: list, runtime=None) -> Engine:
    rt = runtime or DummyRuntime(delay=0)
    return Engine(
        events.append,
        sandbox_factory=LocalSandbox.create,
        runtime_for=lambda backend: rt,
        walk_s=0.01,
        flight_s=0.01,
    )


async def until(cond, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def branches(repo: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "branch", "--format=%(refname:short)"], capture_output=True, text=True)
    return [b for b in out.stdout.split() if b.startswith("orc/")]


def all_idle(engine: Engine) -> bool:
    return all(o.state == "idle" for o in engine.board.orcs.values())


def test_full_cycle_lands_reviewed_branches_in_the_repo(repo):
    async def scenario():
        events: list = []
        engine = make_engine(events)
        engine.boot()  # chieftain, 2 coders, reviewer
        hall = "orc-1"
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "setAutonomy", "id": hall, "on": True})
        await engine.handle({"op": "command", "ids": [hall], "text": "Add a login page"})

        await until(lambda: engine.board.gold == 2 and all_idle(engine))
        await engine.shutdown()
        return engine, events

    engine, events = asyncio.run(scenario())
    types = [e["type"] for e in events]
    assert types.count("orders.sent") == 2
    # Dummy reviewer rejects each first attempt, so every branch went back once.
    assert sum(1 for e in events if e["type"] == "crate.sent" and e["label"] == "findings") == 2
    landed = branches(repo)
    assert len(landed) == 2
    for branch in landed:
        log = subprocess.run(["git", "-C", str(repo), "log", "--oneline", f"main..{branch}"], capture_output=True, text=True)
        assert len(log.stdout.splitlines()) == 2  # first attempt + fix round
    # The user's checkout is untouched: still on main, no stray files.
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout == ""
    assert all(box.destroyed for box in LocalSandbox.created)


def test_supervised_chieftain_waits_and_reject_stands_down(repo):
    async def scenario():
        events: list = []
        engine = make_engine(events)
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "command", "ids": ["orc-1"], "text": "Refactor"})
        await until(lambda: engine.board.orcs["orc-1"].state == "awaiting")
        pending = engine.board.orcs["orc-1"].pending
        await engine.handle({"op": "reject", "id": "orc-1"})
        await until(lambda: all_idle(engine))
        await engine.shutdown()
        return engine, events, pending

    engine, events, pending = asyncio.run(scenario())
    assert pending["kind"] == "plan" and len(pending["items"]) == 2
    assert not any(e["type"] == "orders.sent" for e in events)
    assert engine.board.orcs["orc-1"].pending is None


def test_failure_parks_the_orc_until_retry(repo):
    class FlakyRuntime(DummyRuntime):
        def __init__(self):
            super().__init__(delay=0)
            self.calls = 0

        async def run(self, box, prompt, on_log, *, kind, hints):
            if kind == "code":
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("model fell over")
            return await super().run(box, prompt, on_log, kind=kind, hints={**hints, "attempt": 1})

    async def scenario():
        events: list = []
        engine = make_engine(events, FlakyRuntime())
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "command", "ids": ["orc-2"], "text": "Fix the bug"})
        await until(lambda: engine.board.orcs["orc-2"].state == "failed")
        await engine.handle({"op": "retry", "id": "orc-2"})
        await until(lambda: engine.board.gold == 1 and all_idle(engine))
        await engine.shutdown()
        return engine

    engine = asyncio.run(scenario())
    texts = [line["text"] for line in engine.board.orcs["orc-2"].log]
    assert "model fell over" in texts
    assert len(branches(repo)) == 1


def test_orders_without_a_repo_are_refused():
    async def scenario():
        engine = make_engine([])
        engine.boot()
        await engine.handle({"op": "command", "ids": ["orc-2"], "text": "Anything"})
        await until(lambda: any("No war target" in line["text"] for line in engine.board.orcs["orc-2"].log))
        await until(lambda: all_idle(engine))
        await engine.shutdown()
        return engine

    engine = asyncio.run(scenario())
    assert engine.board.gold == 0
    assert not LocalSandbox.created


def test_bad_repo_path_is_reported(tmp_path):
    async def scenario():
        engine = make_engine([])
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(tmp_path / "nope")})
        await engine.shutdown()
        return engine

    engine = asyncio.run(scenario())
    assert engine.board.repo is None
    assert engine.board.orcs["orc-1"].log[-1]["level"] == "error"
