"""Engine op handling: spawn rules, command routing, approval and abandon paths."""

import asyncio

from orc.runtimes import DummyRuntime
from tests.fakes import LocalSandbox
from tests.test_engine import all_idle, branches, make_engine, repo, until, isolated_git  # noqa: F401


def run(coro):
    return asyncio.run(coro)


def test_spawn_ignores_unknown_roles_and_backends_and_a_second_chieftain():
    async def scenario():
        engine = make_engine([])
        engine.boot()
        before = len(engine.board.orcs)
        await engine.handle({"op": "spawn", "role": "wizard", "backend": "claude"})
        await engine.handle({"op": "spawn", "role": "coder", "backend": "nope"})
        await engine.handle({"op": "spawn", "role": "chieftain", "backend": "claude"})
        unchanged = len(engine.board.orcs)
        await engine.handle({"op": "spawn", "role": "tester", "backend": "claude"})
        await engine.shutdown()
        return engine, before, unchanged

    engine, before, unchanged = run(scenario())
    assert unchanged == before
    assert len(engine.board.orcs) == before + 1


def test_new_reviewer_and_tester_are_linked_for_review():
    async def scenario():
        events: list = []
        engine = make_engine(events)
        engine.boot()
        await engine.handle({"op": "spawn", "role": "tester", "backend": "claude"})
        await engine.handle({"op": "spawn", "role": "reviewer", "backend": "claude"})
        await engine.shutdown()
        return engine, events

    engine, events = run(scenario())
    tester = next(o.id for o in engine.board.orcs.values() if o.role == "tester")
    reviewers = [o.id for o in engine.board.orcs.values() if o.role == "reviewer"]
    edges = {(e["edge"]["from"], e["edge"]["to"], e["edge"]["kind"]) for e in events if e["type"] == "edge.added"}
    assert (tester, reviewers[0], "review") in edges
    assert (tester, reviewers[-1], "review") in edges
    assert ("orc-1", tester, "command") in edges


def test_commanding_a_reviewer_is_refused_and_blank_orders_are_ignored(repo):
    async def scenario():
        engine = make_engine([])
        engine.boot()
        reviewer = next(o.id for o in engine.board.orcs.values() if o.role == "reviewer")
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "command", "ids": [reviewer], "text": "Do a thing"})
        await engine.handle({"op": "command", "ids": ["orc-2"], "text": "   "})
        await engine.handle({"op": "command", "ids": ["ghost"], "text": "Hello"})
        await asyncio.sleep(0.1)
        await engine.shutdown()
        return engine, reviewer

    engine, reviewer = run(scenario())
    assert any(l["level"] == "warn" and "review" in l["text"] for l in engine.board.orcs[reviewer].log)
    assert engine.board.orcs["orc-2"].state == "idle"
    assert not LocalSandbox.created


def test_turning_autonomy_on_approves_a_waiting_plan(repo):
    async def scenario():
        engine = make_engine([])
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "command", "ids": ["orc-1"], "text": "Build"})
        await until(lambda: engine.board.orcs["orc-1"].state == "awaiting")
        await engine.handle({"op": "setAutonomy", "id": "orc-1", "on": True})
        await until(lambda: engine.board.gold == 2 and all_idle(engine))
        await engine.shutdown()
        return engine

    engine = run(scenario())
    assert engine.board.orcs["orc-1"].pending is None
    assert len(branches(repo)) == 2


def test_abandon_after_failure_idles_the_orc_and_destroys_its_box(repo):
    class Broken(DummyRuntime):
        async def run(self, box, prompt, on_log, *, kind, hints):
            raise RuntimeError("no model today")

    async def scenario():
        engine = make_engine([], Broken(delay=0))
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "command", "ids": ["orc-2"], "text": "Fix"})
        await until(lambda: engine.board.orcs["orc-2"].state == "failed")
        await engine.handle({"op": "abandon", "id": "orc-2"})
        await until(lambda: all_idle(engine))
        await engine.shutdown()
        return engine

    engine = run(scenario())
    texts = [l["text"] for l in engine.board.orcs["orc-2"].log]
    assert "no model today" in texts
    assert any("abandoned" in t for t in texts)
    assert engine.board.gold == 0
    assert branches(repo) == []
    assert all(b.destroyed for b in LocalSandbox.created)


def test_ops_for_unknown_ids_are_harmless():
    async def scenario():
        engine = make_engine([])
        engine.boot()
        for op in ({"op": "approve", "id": "x"}, {"op": "retry", "id": "x"},
                   {"op": "setAutonomy", "id": "x", "on": True}, {"op": "merge", "id": "x"},
                   {"op": "diff", "id": "x"}, {"op": "bogus"}):
            await engine.handle(op)
        await engine.shutdown()
        return engine

    engine = run(scenario())
    assert "x" not in engine.board.orcs
