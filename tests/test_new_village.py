import asyncio
import json

from orc.engine import Engine, slot
from orc.persist import BoardStore
from orc.runtimes import DummyRuntime
from tests.fakes import LocalSandbox
from tests.test_engine import all_idle, branches, isolated_git, repo, until  # noqa: F401  (fixtures)

DEFAULT = [("orc-1", "chieftain"), ("orc-2", "coder"), ("orc-3", "coder"), ("orc-4", "reviewer")]


class StuckRuntime:
    """Works in its box, then never finishes: a job that is still running when the village is reset."""

    name = "stuck"

    def __init__(self) -> None:
        self.started = 0

    async def run(self, box, prompt, on_log, *, kind, hints) -> str:
        on_log("$ thinking very hard", "cmd")
        self.started += 1
        await asyncio.Event().wait()
        return ""


def make_engine(events: list, runtime, store: BoardStore | None = None) -> Engine:
    return Engine(
        events.append,
        sandbox_factory=LocalSandbox.create,
        runtime_for=lambda backend: runtime,
        walk_s=0.01,
        flight_s=0.01,
        store=store,
    )


def test_new_village_stops_work_destroys_boxes_and_founds_the_default(repo, tmp_path):
    store = BoardStore(tmp_path / "board.json")
    runtime = StuckRuntime()

    async def scenario():
        events: list = []
        engine = make_engine(events, runtime, store)
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "spawn", "role": "tester", "backend": "cursor"})
        engine._emit("gold.banked", amount=3)
        # orc-2 is mid-job in a box; orc-3 is waiting at an approval gate.
        await engine.handle({"op": "command", "ids": ["orc-2"], "text": "Add a login page"})
        await engine.handle({"op": "setAutonomy", "id": "orc-3", "on": False})
        await engine.handle({"op": "command", "ids": ["orc-3"], "text": "Add a logout page"})
        await until(lambda: runtime.started == 1 and "orc-3" in engine.gates)
        busy_box = next(w.box for w in engine.works.values() if w.owner == "orc-2")
        old_workers = list(engine.workers.values())
        # A handoff still in flight (like gold landing after a walk) must not reach the new village.
        engine._later(0.05, engine._emit, "gold.banked", amount=1)

        events.clear()
        await engine.handle({"op": "newVillage"})
        saved = json.loads(store.path.read_text())  # saved right away, not on the debounce timer
        await asyncio.sleep(0.1)
        await engine.shutdown()
        return engine, events, busy_box, old_workers, saved

    engine, events, busy_box, old_workers, saved = asyncio.run(scenario())
    board = engine.board

    assert busy_box.destroyed
    assert all(box.destroyed for box in LocalSandbox.created)
    assert all(t.cancelled() for t in old_workers)
    assert not engine.gates and not engine.decisions and not engine.works

    assert [(o.id, o.role) for o in board.orcs.values()] == DEFAULT
    assert all(o.state == "idle" and o.task is None and o.pending is None for o in board.orcs.values())
    assert board.repo is None and board.gold == 0
    assert {(e.source, e.target, e.kind) for e in board.edges} == {
        ("orc-1", "orc-2", "command"), ("orc-1", "orc-3", "command"), ("orc-1", "orc-4", "command"),
        ("orc-2", "orc-4", "review"), ("orc-3", "orc-4", "review"),
    }
    assert not any("login" in line["text"] for o in board.orcs.values() for line in o.log)
    assert any("2 unfinished jobs were stopped" in line["text"] for line in board.orcs["orc-1"].log)

    # Clients saw it all as ordinary events, starting with a reset.
    assert events[0] == {"type": "board.reset"}
    assert [e["orc"]["id"] for e in events if e["type"] == "orc.spawned"] == [oid for oid, _ in DEFAULT]

    assert saved["repo"] is None and saved["gold"] == 0
    assert [(o["id"], o["role"]) for o in saved["orcs"]] == DEFAULT


def test_spawns_after_a_new_village_number_from_scratch(repo):
    async def scenario():
        engine = make_engine([], DummyRuntime(delay=0))
        engine.boot()
        for _ in range(3):
            await engine.handle({"op": "spawn", "role": "coder", "backend": "claude"})
        await engine.handle({"op": "newVillage"})
        await engine.handle({"op": "spawn", "role": "tester", "backend": "cursor"})
        await engine.shutdown()
        return engine.board

    board = asyncio.run(scenario())
    newcomer = board.orcs["orc-5"]
    assert len(board.orcs) == 5
    assert newcomer.hut == slot(3)  # the hut ring restarts too
    assert newcomer.name not in {o.name for o in board.orcs.values() if o.id != "orc-5"}


def test_delivered_branches_survive_a_new_village(repo):
    async def scenario():
        engine = make_engine([], DummyRuntime(delay=0))
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "command", "ids": ["orc-2"], "text": "Add a login page"})
        await until(lambda: engine.board.gold == 1 and all_idle(engine))
        delivered = branches(repo)
        await engine.handle({"op": "newVillage"})
        await engine.shutdown()
        return delivered

    delivered = asyncio.run(scenario())
    assert len(delivered) == 1
    assert branches(repo) == delivered
