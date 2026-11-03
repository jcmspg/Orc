"""Branches of the orcs Durg and Mogka (`orc/durg/*`, `orc/mogka/*`): fetch, merge and restore."""

import asyncio
import json
import subprocess

import pytest

from orc import gitops
from orc.board import Board
from orc.persist import BoardStore
from tests.test_engine import isolated_git, repo  # noqa: F401  (fixtures)
from tests.test_gitops import commit_on, git, snapshot
from tests.test_persist import make_engine

DURG = "orc/durg/login-1a2b3c"
MOGKA = "orc/mogka/logout-4d5e6f"


def make_bundle(repo, tmp_path, branch, files):
    """A box's delivery: a bundle holding one branch cut from main, made without touching `repo`."""
    clone = tmp_path / f"box-{branch.replace('/', '-')}"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    git(clone, "checkout", "-q", "-b", branch)
    for name, text in files.items():
        (clone / name).write_text(text)
    git(clone, "add", "-A")
    git(clone, "commit", "-qm", f"work on {branch}")
    bundle = tmp_path / f"{branch.replace('/', '-')}.bundle"
    git(clone, "bundle", "create", str(bundle), branch)
    return bundle, git(clone, "rev-parse", branch)


@pytest.mark.parametrize("branch", [DURG, MOGKA])
def test_fetching_a_bundle_creates_the_branch_and_leaves_the_tree_alone(repo, tmp_path, branch):
    bundle, tip = make_bundle(repo, tmp_path, branch, {"a.py": "a\n"})
    (repo / "README.md").write_text("wip\n")
    before = snapshot(repo)
    assert gitops.branch_tip(str(repo), branch) is None

    gitops.fetch_branch(str(repo), bundle, branch)

    assert gitops.branch_tip(str(repo), branch) == tip
    assert snapshot(repo) == before


def test_each_orcs_bundle_only_brings_its_own_branch(repo, tmp_path):
    d_bundle, d_tip = make_bundle(repo, tmp_path, DURG, {"d.py": "d\n"})
    m_bundle, m_tip = make_bundle(repo, tmp_path, MOGKA, {"m.py": "m\n"})
    gitops.fetch_branch(str(repo), d_bundle, DURG)
    gitops.fetch_branch(str(repo), m_bundle, MOGKA)
    assert git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/orc/") == f"{DURG}\n{MOGKA}"
    assert (gitops.branch_tip(str(repo), DURG), gitops.branch_tip(str(repo), MOGKA)) == (d_tip, m_tip)


def test_fetching_a_branch_missing_from_the_bundle_fails(repo, tmp_path):
    bundle, _ = make_bundle(repo, tmp_path, DURG, {"d.py": "d\n"})
    with pytest.raises(RuntimeError):
        gitops.fetch_branch(str(repo), bundle, MOGKA)
    assert gitops.branch_tip(str(repo), MOGKA) is None


def test_merging_both_orcs_in_turn_keeps_both_changes(repo):
    git(repo, "branch", "develop")
    d = commit_on(repo, DURG, {"d.py": "d\n"}, "Durg's work", start="develop")
    m = commit_on(repo, MOGKA, {"m.py": "m\n"}, "Mogka's work", start="develop")
    before = snapshot(repo)

    first = gitops.merge(str(repo), DURG, "develop", expect=d)
    second = gitops.merge(str(repo), MOGKA, "develop", expect=m)

    assert (first.status, second.status) == ("merged", "merged")
    assert set(git(repo, "ls-tree", "--name-only", "develop").split()) == {"README.md", "d.py", "m.py"}
    assert git(repo, "rev-parse", f"{second.commit}^1") == first.commit
    assert gitops.is_ancestor(str(repo), d, "develop") and gitops.is_ancestor(str(repo), m, "develop")
    assert snapshot(repo) == before


def test_the_second_orc_conflicts_with_the_first_after_it_merges(repo):
    git(repo, "branch", "develop")
    d = commit_on(repo, DURG, {"shared.py": "durg\n"}, "Durg", start="develop")
    m = commit_on(repo, MOGKA, {"shared.py": "mogka\n"}, "Mogka", start="develop")
    assert gitops.merge(str(repo), DURG, "develop", expect=d).status == "merged"
    tip = git(repo, "rev-parse", "develop")

    result = gitops.merge(str(repo), MOGKA, "develop", expect=m)

    assert (result.status, result.conflicts) == ("conflict", ["shared.py"])
    assert git(repo, "rev-parse", "develop") == tip


def test_similarly_named_branches_are_not_confused(repo):
    git(repo, "branch", "develop")
    commit_on(repo, DURG, {"d.py": "d\n"}, "Durg", start="develop")
    assert gitops.branch_tip(str(repo), "orc/durg") is None
    assert gitops.branch_tip(str(repo), "orc/mogka/login-1a2b3c") is None
    with pytest.raises(RuntimeError, match="doesn't exist"):
        gitops.merge(str(repo), "orc/mogka/login-1a2b3c", "develop")


def _orc(id_, name):
    return {"id": id_, "name": name, "role": "coder", "backend": "claude", "autonomy": False, "hut": {"x": 0, "y": 0}}


def _delivery(id_, branch, owner, repo, base, head):
    return {"id": id_, "branch": branch, "task": "t", "owner": owner, "repo": str(repo), "base": base,
            "baseBranch": "main", "head": head, "stat": "1 file changed"}


def test_restoring_a_village_with_durg_and_mogka(repo, tmp_path):
    base = git(repo, "rev-parse", "main")
    d = commit_on(repo, DURG, {"d.py": "d\n"}, "Durg")
    m = commit_on(repo, MOGKA, {"m.py": "m\n"}, "Mogka")
    board = Board()
    board.apply({"type": "orc.spawned", "orc": _orc("orc-1", "Durg")})
    board.apply({"type": "orc.spawned", "orc": _orc("orc-2", "Mogka")})
    board.apply({"type": "board.repo", "repo": str(repo)})
    for did, branch, owner, head in (("d1", DURG, "orc-1", d), ("m1", MOGKA, "orc-2", m)):
        board.apply({"type": "branch.delivered", "delivery": _delivery(did, branch, owner, repo, base, head)})
    store = BoardStore(tmp_path / "board.json")
    store.save(board)
    assert {x["branch"] for x in json.loads(store.path.read_text())["deliveries"]} == {DURG, MOGKA}

    # While orc was closed: Mogka's branch was moved by hand, Durg's is untouched.
    moved = commit_on(repo, MOGKA, {"m.py": "hand edit\n"}, "Hand edit")

    async def launch():
        events: list = []
        engine = make_engine(events, store)
        engine.boot()
        await engine.shutdown()
        return engine.board

    after = asyncio.run(launch())

    assert after.deliveries["d1"].status == "delivered"
    assert after.deliveries["d1"].head == d
    assert after.deliveries["m1"].status == "moved"
    assert after.deliveries["m1"].head == m != moved
    assert {o.name for o in after.orcs.values()} >= {"Durg", "Mogka"}


def test_restored_delivery_can_still_be_merged(repo, tmp_path):
    base = git(repo, "rev-parse", "main")
    git(repo, "branch", "develop")
    m = commit_on(repo, MOGKA, {"m.py": "m\n"}, "Mogka", start="develop")
    board = Board()
    board.apply({"type": "orc.spawned", "orc": _orc("orc-1", "Mogka")})
    board.apply({"type": "board.repo", "repo": str(repo)})
    board.apply({"type": "branch.delivered", "delivery": _delivery("m1", MOGKA, "orc-1", repo, base, m)})
    store = BoardStore(tmp_path / "board.json")
    store.save(board)

    async def launch_and_merge():
        engine = make_engine([], store)
        engine.boot()
        d = engine.board.deliveries["m1"]
        result = gitops.merge(d.repo, d.branch, "develop", expect=d.head)
        await engine.shutdown()
        return result

    assert asyncio.run(launch_and_merge()).status == "merged"
    assert gitops.is_ancestor(str(repo), m, "develop")
