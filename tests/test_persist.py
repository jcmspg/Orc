import asyncio
import json
import stat
import subprocess

import pytest

from orc.board import Board
from orc.engine import Engine
from orc.persist import BoardStore, state_path
from orc.runtimes import DummyRuntime
from tests.fakes import LocalSandbox
from tests.test_engine import all_idle, isolated_git, repo, until  # noqa: F401  (fixtures)
from tests.test_spoils import deliver_two


def make_engine(events: list, store: BoardStore, runtime=None) -> Engine:
    rt = runtime or DummyRuntime(delay=0)
    return Engine(
        events.append,
        sandbox_factory=LocalSandbox.create,
        runtime_for=lambda backend: rt,
        walk_s=0.01,
        flight_s=0.01,
        store=store,
    )


def test_village_survives_a_restart(repo, tmp_path):
    store = BoardStore(tmp_path / "board.json")

    async def first_launch():
        engine = make_engine([], store)
        engine.boot()
        await engine.handle({"op": "setRepo", "repo": str(repo)})
        await engine.handle({"op": "spawn", "role": "tester", "backend": "cursor"})
        await engine.handle({"op": "setAutonomy", "id": "orc-1", "on": True})
        await engine.handle({"op": "command", "ids": ["orc-1"], "text": "Add a login page"})
        await until(lambda: engine.board.gold == 3 and all_idle(engine))
        await engine.shutdown()
        return engine.board

    async def second_launch():
        events: list = []
        engine = make_engine(events, store)
        engine.boot()
        engine.spawn("coder", "claude")  # new units continue the numbering and hut ring
        await engine.shutdown()
        return engine, events

    before = asyncio.run(first_launch())
    engine, events = asyncio.run(second_launch())
    after = engine.board

    assert after.repo == before.repo == str(repo)
    assert after.gold == 3
    assert [(o.id, o.name, o.role, o.backend, o.autonomy, o.hut) for o in list(after.orcs.values())[:5]] == [
        (o.id, o.name, o.role, o.backend, o.autonomy, o.hut) for o in before.orcs.values()
    ]
    assert [e.to_dict() for e in after.edges[: len(before.edges)]] == [e.to_dict() for e in before.edges]
    assert after.orcs["orc-1"].log[0]["text"] == before.orcs["orc-1"].log[0]["text"]
    newcomer = after.orcs["orc-6"]
    assert newcomer.name not in {o.name for o in before.orcs.values()}
    assert newcomer.hut not in [o.hut for o in before.orcs.values()]
    # The restore went out as ordinary events, so a client watching boot sees the same village.
    assert [e["type"] for e in events].count("orc.spawned") == 6


def test_interrupted_work_is_reported_not_resumed(tmp_path):
    store = BoardStore(tmp_path / "board.json")
    board = Board()
    board.apply({"type": "orc.spawned", "orc": {
        "id": "orc-1", "name": "Ozgar", "role": "chieftain", "backend": "claude", "autonomy": False,
        "hut": {"x": 0, "y": 0}, "state": "awaiting", "task": "Add a login page",
        "pending": {"kind": "plan", "summary": "s", "items": []},
    }})
    store.save(board)

    async def launch():
        engine = make_engine([], store)
        engine.boot()
        await engine.shutdown()
        return engine.board

    orc = asyncio.run(launch()).orcs["orc-1"]
    assert (orc.state, orc.task, orc.pending) == ("idle", None, None)
    assert any("Add a login page" in line["text"] and line["level"] == "warn" for line in orc.log)


def test_vanished_war_target_is_cleared(tmp_path):
    store = BoardStore(tmp_path / "board.json")
    board = Board()
    board.apply({"type": "orc.spawned", "orc": {
        "id": "orc-1", "name": "Ozgar", "role": "chieftain", "backend": "claude", "autonomy": False, "hut": {"x": 0, "y": 0},
    }})
    board.apply({"type": "board.repo", "repo": str(tmp_path / "deleted")})
    store.save(board)

    async def launch():
        engine = make_engine([], store)
        engine.boot()
        await engine.shutdown()
        return engine.board

    board = asyncio.run(launch())
    assert board.repo is None
    assert any("is gone" in line["text"] for line in board.orcs["orc-1"].log)


def test_corrupt_save_is_kept_aside_and_reported(tmp_path):
    path = tmp_path / "board.json"
    path.write_text("{not json")
    store = BoardStore(path)

    async def launch():
        engine = make_engine([], store)
        engine.boot()
        await engine.shutdown()
        return engine.board

    board = asyncio.run(launch())
    assert len(board.orcs) == 4  # a fresh default village
    assert (tmp_path / "board.json.bad").read_text() == "{not json"
    assert any(line["level"] == "error" and "board.json.bad" in line["text"] for line in board.orcs["orc-1"].log)
    assert json.loads(path.read_text())["version"] == 1  # and the new village was saved


def test_save_file_is_private_and_lives_in_xdg_state(tmp_path):
    assert state_path() == tmp_path / "state" / "orc" / "board.json"  # conftest points XDG_STATE_HOME here
    store = BoardStore()
    store.save(Board())
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_deliveries_survive_a_restart_and_are_rechecked(repo, tmp_path):
    store = BoardStore(tmp_path / "board.json")

    async def first_launch():
        engine = await deliver_two(repo, [])
        engine.store = store
        await engine.shutdown()
        return engine.board

    async def second_launch():
        events: list = []
        engine = make_engine(events, store)
        engine.boot()
        await engine.shutdown()
        return engine.board, events

    before = asyncio.run(first_launch())
    kept, deleted = list(before.deliveries.values())
    saved = json.loads(store.path.read_text())["deliveries"]
    assert [d["id"] for d in saved] == [kept.id, deleted.id] and saved[0]["baseBranch"] == "main"
    subprocess.run(["git", "-C", str(repo), "branch", "-D", deleted.branch], check=True)  # while orc was closed

    after, events = asyncio.run(second_launch())
    assert after.deliveries[kept.id] == kept
    assert after.deliveries[deleted.id].status == "missing"
    assert [e["type"] for e in events].count("branch.delivered") == 2
    assert any("was deleted outside orc" in line["text"] for line in after.orcs["orc-1"].log)


def test_a_bad_delivery_entry_is_a_bad_save(tmp_path):
    store = BoardStore(tmp_path / "board.json")
    store.save(Board())
    data = json.loads(store.path.read_text())
    data["deliveries"] = [{"id": "x", "status": "delivered"}]
    store.path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="bad delivery"):
        store.load()


def test_saves_from_before_deliveries_still_load(tmp_path):
    store = BoardStore(tmp_path / "board.json")
    store.save(Board())
    data = json.loads(store.path.read_text())
    del data["deliveries"]
    store.path.write_text(json.dumps(data))
    assert store.load().deliveries == []


def test_valid_json_that_is_not_a_village_is_set_aside(tmp_path):
    for junk in ("[]", "null", '"hello"', "42"):
        path = tmp_path / "board.json"
        path.write_text(junk)

        async def launch():
            engine = make_engine([], BoardStore(path))
            engine.boot()
            await engine.shutdown()
            return engine.board

        board = asyncio.run(launch())
        assert len(board.orcs) == 4, junk  # a fresh village instead of a crash on boot
        assert (tmp_path / "board.json.bad").read_text() == junk


def test_a_failing_save_is_reported_once_and_does_not_crash(tmp_path):
    class Broken(BoardStore):
        def save(self, board):
            raise OSError("disk full")

    async def scenario():
        engine = make_engine([], Broken(tmp_path / "board.json"))
        engine.boot()
        engine._save()
        engine._save()
        await engine.shutdown()  # the final save fails too, quietly
        return engine.board

    board = asyncio.run(scenario())
    errors = [line["text"] for line in board.orcs["orc-1"].log if "Can't save" in line["text"]]
    assert len(errors) == 1 and "disk full" in errors[0]
