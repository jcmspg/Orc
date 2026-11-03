"""Delivered branches (the spoils): recorded on the board, diffed and merged from the app."""

import asyncio
import subprocess
from pathlib import Path

from orc.engine import Engine
from orc.runtimes import DummyRuntime
from tests.test_engine import all_idle, isolated_git, make_engine, repo, until  # noqa: F401  (fixtures)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


class TaskRuntime(DummyRuntime):
    """Like the dummy, but writes its task into the file, so two branches always conflict."""

    async def run(self, box, prompt, on_log, *, kind, hints):
        if kind == "code":
            await box.exec(["sh", "-c", 'echo "$1" >> ORC_DUMMY.md', "sh", hints["task"]])
            return "Wrote the task down."
        return await super().run(box, prompt, on_log, kind=kind, hints=hints)


async def deliver_two(repo: Path, events: list) -> Engine:
    """Run a full job: two coders, each delivers one branch."""
    engine = make_engine(events, TaskRuntime(delay=0))
    engine.boot()
    await engine.handle({"op": "setRepo", "repo": str(repo)})
    await engine.handle({"op": "setAutonomy", "id": "orc-1", "on": True})
    await engine.handle({"op": "command", "ids": ["orc-1"], "text": "Add a login page"})
    await until(lambda: engine.board.gold == 2 and all_idle(engine))
    return engine


def last(events: list, type_: str, **match) -> dict:
    return next(e for e in reversed(events) if e["type"] == type_ and all(e.get(k) == v for k, v in match.items()))


def test_full_cycle_records_deliveries(repo):
    base = git(repo, "rev-parse", "main")

    async def scenario():
        events: list = []
        engine = await deliver_two(repo, events)
        await engine.shutdown()
        return engine, events

    engine, events = asyncio.run(scenario())
    deliveries = list(engine.board.deliveries.values())
    assert len(deliveries) == 2
    for d in deliveries:
        assert d.status == "delivered"
        assert (d.base, d.base_branch, d.repo) == (base, "main", str(repo))
        assert d.head == git(repo, "rev-parse", d.branch)
        assert "1 file changed" in d.stat
        assert engine.board.orcs[d.owner].role == "coder"
        assert d.task.endswith("Add a login page")
    wire = last(events, "branch.delivered")["delivery"]
    assert {"baseBranch", "mergeCommit"} <= wire.keys() and "base_branch" not in wire
    # A fresh client gets the deliveries in its snapshot.
    assert sum(1 for e in engine.board.snapshot() if e["type"] == "branch.delivered") == 2


def test_diff_op_sends_the_branch_diff(repo):
    async def scenario():
        events: list = []
        engine = await deliver_two(repo, events)
        did = next(iter(engine.board.deliveries))
        await engine.handle({"op": "diff", "id": did})
        await engine.handle({"op": "diff", "id": "no-such-delivery"})
        await engine.shutdown()
        return did, events

    did, events = asyncio.run(scenario())
    diffs = [e for e in events if e["type"] == "branch.diff"]
    assert len(diffs) == 1
    assert diffs[0]["id"] == did and diffs[0]["error"] is None and not diffs[0]["truncated"]
    assert "diff --git a/ORC_DUMMY.md b/ORC_DUMMY.md" in diffs[0]["text"]


def test_merge_into_checked_out_branch_hands_over_the_command(repo):
    before = git(repo, "rev-parse", "main")

    async def scenario():
        events: list = []
        engine = await deliver_two(repo, events)
        did = next(iter(engine.board.deliveries))
        await engine.handle({"op": "merge", "id": did})
        await engine.shutdown()
        return engine, did, events

    engine, did, events = asyncio.run(scenario())
    d = engine.board.deliveries[did]
    assert d.status == "delivered"
    assert d.command == f"git merge --no-ff {d.branch}"
    assert "main is checked out in" in (d.note or "")
    assert git(repo, "rev-parse", "main") == before
    assert git(repo, "status", "--porcelain") == ""
    assert last(events, "branch.status", id=did)["command"] == d.command


def test_merge_into_a_free_branch_then_conflict(repo):
    async def scenario():
        events: list = []
        engine = await deliver_two(repo, events)
        git(repo, "checkout", "-q", "-b", "elsewhere")  # the player moved on; main is free
        first, second = list(engine.board.deliveries)
        await engine.handle({"op": "merge", "id": first})
        await engine.handle({"op": "merge", "id": second})  # both dummies appended to ORC_DUMMY.md
        await engine.shutdown()
        return engine, first, second, events

    engine, first, second, events = asyncio.run(scenario())
    a, b = engine.board.deliveries[first], engine.board.deliveries[second]
    assert a.status == "merged" and a.merge_commit == git(repo, "rev-parse", "main")
    assert last(events, "branch.merged", id=first) == {"type": "branch.merged", "id": first, "into": "main", "commit": a.merge_commit}
    assert b.status == "conflict" and b.conflicts == ["ORC_DUMMY.md"]
    assert b.command == f"git switch main && git merge --no-ff {b.branch}"
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "elsewhere")
    assert git(repo, "status", "--porcelain") == ""
    hall = [line["text"] for line in engine.board.orcs["orc-1"].log]
    assert any(t.startswith(f"Merged {a.branch} into main") for t in hall)
    assert any("ORC_DUMMY.md" in t and "Nothing was merged" in t for t in hall)


def test_detached_head_has_no_merge_target(repo):
    git(repo, "checkout", "-q", "--detach")

    async def scenario():
        engine = await deliver_two(repo, [])
        did = next(iter(engine.board.deliveries))
        await engine.handle({"op": "merge", "id": did})
        await engine.shutdown()
        return engine.board.deliveries[did]

    d = asyncio.run(scenario())
    assert d.base_branch is None
    assert "detached HEAD" in (d.note or "") and d.command == f"git merge --no-ff {d.branch}"


def test_branches_changed_outside_orc_are_reported(repo):
    async def scenario():
        events: list = []
        engine = await deliver_two(repo, events)
        gone, moved = list(engine.board.deliveries.values())
        git(repo, "branch", "-D", gone.branch)
        git(repo, "branch", "-f", moved.branch, "main")  # reset by hand: the work is no longer on it
        await engine.handle({"op": "merge", "id": gone.id})
        await engine.handle({"op": "diff", "id": moved.id})
        git(repo, "branch", "-f", moved.branch, moved.head)  # put it back
        await engine.handle({"op": "diff", "id": moved.id})
        await engine.shutdown()
        return engine, gone.id, moved.id, events

    engine, gone, moved, events = asyncio.run(scenario())
    assert engine.board.deliveries[gone].status == "missing"
    assert "Deleted outside orc" in (engine.board.deliveries[gone].note or "")
    statuses = [e["status"] for e in events if e["type"] == "branch.status" and e["id"] == moved]
    assert statuses == ["moved", "delivered"]  # reset by hand, then put back
    assert any("was deleted outside orc" in line["text"] for line in engine.board.orcs["orc-1"].log)


def test_a_moved_branch_is_not_merged(repo):
    async def scenario():
        engine = await deliver_two(repo, [])
        git(repo, "checkout", "-q", "-b", "elsewhere")
        d = next(iter(engine.board.deliveries.values()))
        scratch = repo.parent / "hand"
        git(repo, "worktree", "add", "-q", str(scratch), d.branch)
        (scratch / "extra.txt").write_text("by hand\n")
        git(scratch, "add", "extra.txt")
        git(scratch, "commit", "-qm", "hand edit")
        git(repo, "worktree", "remove", str(scratch))
        main = git(repo, "rev-parse", "main")
        await engine.handle({"op": "merge", "id": d.id})
        await engine.shutdown()
        return engine.board.deliveries[d.id], main

    d, main = asyncio.run(scenario())
    assert d.status == "moved" and d.command == f"git merge --no-ff {d.branch}"
    assert git(repo, "rev-parse", "main") == main


def test_bundled_delivery_diffs_from_its_bundle_and_never_merges(repo, tmp_path):
    base = git(repo, "rev-parse", "main")
    git(repo, "checkout", "-q", "-b", "orc/durg/x-abc123")
    (repo / "x.txt").write_text("x\n")
    git(repo, "add", "x.txt")
    git(repo, "commit", "-qm", "x")
    head = git(repo, "rev-parse", "HEAD")
    bundle = tmp_path / "abc123.bundle"
    git(repo, "bundle", "create", str(bundle), "orc/durg/x-abc123")
    delivery = {
        "id": "abc123", "branch": "orc/durg/x-abc123", "task": "x", "owner": "orc-2",
        "repo": "https://example.com/r.git", "base": base, "baseBranch": "main", "head": head, "stat": "1 file changed",
        "status": "bundled", "note": "remote", "command": "git fetch ...", "bundle": str(bundle),
    }

    async def scenario():
        events: list = []
        engine = make_engine(events)
        engine.boot()
        engine._emit("branch.delivered", delivery=delivery)
        await engine.handle({"op": "diff", "id": "abc123"})
        await engine.handle({"op": "merge", "id": "abc123"})
        await engine.shutdown()
        return engine, events

    engine, events = asyncio.run(scenario())
    assert "+x" in last(events, "branch.diff", id="abc123")["text"]
    assert engine.board.deliveries["abc123"].status == "bundled"
    assert last(events, "branch.status", id="abc123")["command"] == "git fetch ..."


def test_a_branch_merged_by_hand_is_marked_merged(repo):
    async def scenario():
        engine = await deliver_two(repo, [])
        d = next(iter(engine.board.deliveries.values()))
        git(repo, "merge", "-q", "--no-ff", "-m", "by hand", d.branch)  # the player's own merge, on main
        await engine.handle({"op": "merge", "id": d.id})
        await engine.shutdown()
        return engine.board.deliveries[d.id]

    d = asyncio.run(scenario())
    assert d.status == "merged" and d.note == "Merged into main outside orc."


def test_generated_commands_quote_branch_names(repo):
    # git allows shell metacharacters in branch names; the Copy button must not carry them into a terminal.
    git(repo, "checkout", "-q", "-b", "feat;x&y")

    async def scenario():
        events: list = []
        engine = await deliver_two(repo, events)
        git(repo, "checkout", "-q", "-b", "elsewhere")
        first, second = list(engine.board.deliveries)
        await engine.handle({"op": "merge", "id": first})
        await engine.handle({"op": "merge", "id": second})
        await engine.shutdown()
        return engine, second

    engine, second = asyncio.run(scenario())
    assert engine.board.deliveries[second].command == f"git switch 'feat;x&y' && git merge --no-ff {engine.board.deliveries[second].branch}"
