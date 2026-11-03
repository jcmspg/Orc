"""gitops against real temp repos: in-app merges must never touch a working tree or an index."""

import subprocess
from pathlib import Path

import pytest

from orc import gitops
from tests.test_engine import isolated_git, repo  # noqa: F401  (fixtures)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def commit_on(repo: Path, branch: str, files: dict[str, str], message: str, start: str = "main") -> str:
    """Commit on `branch` without touching the repo's working tree: build it in a scratch clone and fetch."""
    scratch = repo.parent / f"scratch-{branch.replace('/', '-')}"
    if not scratch.exists():
        subprocess.run(["git", "clone", "-q", str(repo), str(scratch)], check=True)
    git(scratch, "fetch", "-q", "origin", "+refs/heads/*:refs/remotes/origin/*")
    git(scratch, "checkout", "-q", "-B", branch, f"origin/{branch}" if gitops.branch_tip(str(repo), branch) else f"origin/{start}")
    for name, text in files.items():
        (scratch / name).parent.mkdir(parents=True, exist_ok=True)
        (scratch / name).write_text(text)
    git(scratch, "add", "-A")
    git(scratch, "commit", "-qm", message)
    git(repo, "fetch", "-q", "--update-head-ok", str(scratch), f"+refs/heads/{branch}:refs/heads/{branch}")
    return git(repo, "rev-parse", branch)


def snapshot(repo: Path) -> tuple[str, str, dict[str, str]]:
    """Everything a merge must leave alone: status, HEAD and every file in the working tree."""
    files = {str(p.relative_to(repo)): p.read_text() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    return git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"), files


@pytest.fixture
def dirty(repo):
    """The player has uncommitted work in progress, staged and unstaged, on `main`."""
    (repo / "README.md").write_text("hello\nwork in progress\n")
    (repo / "notes.txt").write_text("staged\n")
    git(repo, "add", "notes.txt")
    return repo


def test_merges_into_a_branch_nobody_has_checked_out(dirty):
    repo = dirty
    git(repo, "branch", "develop")
    base = git(repo, "rev-parse", "develop")
    tip = commit_on(repo, "orc/durg/login-1a2b3c", {"login.py": "def login(): ...\n"}, "Add login", start="develop")
    commit_on(repo, "develop", {"other.py": "x = 1\n"}, "Unrelated work")
    before = snapshot(repo)

    result = gitops.merge(str(repo), "orc/durg/login-1a2b3c", "develop", expect=tip)

    assert result.status == "merged"
    assert git(repo, "rev-parse", "develop") == result.commit
    parents = git(repo, "rev-list", "--parents", "-n", "1", result.commit).split()[1:]
    assert parents[1] == tip and parents[0] != base
    assert set(git(repo, "ls-tree", "--name-only", result.commit).split()) == {"README.md", "login.py", "other.py"}
    assert "Merge branch 'orc/durg/login-1a2b3c' into develop" in git(repo, "log", "-1", "--format=%s", "develop")
    assert snapshot(repo) == before  # working tree, index and HEAD untouched
    assert gitops.merge(str(repo), "orc/durg/login-1a2b3c", "develop").status == "upToDate"


def test_refuses_when_the_target_is_checked_out(dirty):
    repo = dirty
    tip = commit_on(repo, "orc/durg/login-1a2b3c", {"login.py": "x\n"}, "Add login")
    main = git(repo, "rev-parse", "main")
    before = snapshot(repo)

    result = gitops.merge(str(repo), "orc/durg/login-1a2b3c", "main", expect=tip)

    assert result.status == "checkedOut"
    assert result.worktrees == [str(repo)]
    assert result.conflicts == []
    assert git(repo, "rev-parse", "main") == main
    assert snapshot(repo) == before


def test_refuses_when_the_target_is_checked_out_in_another_worktree(repo, tmp_path):
    git(repo, "branch", "develop")
    git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "develop")
    commit_on(repo, "orc/durg/login-1a2b3c", {"login.py": "x\n"}, "Add login", start="develop")
    develop = git(repo, "rev-parse", "develop")

    result = gitops.merge(str(repo), "orc/durg/login-1a2b3c", "develop")

    assert result.status == "checkedOut"
    assert result.worktrees == [str(tmp_path / "wt")]
    assert git(repo, "rev-parse", "develop") == develop


def test_conflicts_are_named_and_nothing_moves(dirty):
    repo = dirty
    git(repo, "branch", "develop")
    commit_on(repo, "orc/durg/readme-1a2b3c", {"README.md": "orc version\n", "new.py": "y\n"}, "Edit readme", start="develop")
    develop = commit_on(repo, "develop", {"README.md": "player version\n"}, "Player edit")
    before = snapshot(repo)

    result = gitops.merge(str(repo), "orc/durg/readme-1a2b3c", "develop")

    assert result.status == "conflict"
    assert result.conflicts == ["README.md"]
    assert git(repo, "rev-parse", "develop") == develop
    assert snapshot(repo) == before


def test_checked_out_target_still_reports_conflicts(repo):
    commit_on(repo, "orc/durg/readme-1a2b3c", {"README.md": "orc version\n"}, "Edit readme")
    commit_on(repo, "main", {"README.md": "player version\n"}, "Player edit")
    result = gitops.merge(str(repo), "orc/durg/readme-1a2b3c", "main")
    assert (result.status, result.conflicts) == ("checkedOut", ["README.md"])


def test_a_concurrent_move_of_the_target_fails_safely(repo, monkeypatch):
    git(repo, "branch", "develop")
    commit_on(repo, "orc/durg/login-1a2b3c", {"login.py": "x\n"}, "Add login", start="develop")
    real = gitops.checked_out_at

    def player_commits_meanwhile(r, branch):
        commit_on(repo, "develop", {"other.py": "y\n"}, "Player commit")
        return real(r, branch)

    monkeypatch.setattr(gitops, "checked_out_at", player_commits_meanwhile)
    with pytest.raises(RuntimeError, match="update-ref"):
        gitops.merge(str(repo), "orc/durg/login-1a2b3c", "develop")
    assert git(repo, "log", "-1", "--format=%s", "develop") == "Player commit"


def test_a_branch_that_moved_is_not_merged(repo):
    git(repo, "branch", "develop")
    first = commit_on(repo, "orc/durg/login-1a2b3c", {"login.py": "x\n"}, "Add login", start="develop")
    commit_on(repo, "orc/durg/login-1a2b3c", {"login.py": "changed by hand\n"}, "Hand edit")
    with pytest.raises(RuntimeError, match="not .* as delivered"):
        gitops.merge(str(repo), "orc/durg/login-1a2b3c", "develop", expect=first)


def test_diff_reads_commits_not_the_working_tree(dirty):
    repo = dirty
    base = git(repo, "rev-parse", "main")
    commit_on(repo, "orc/durg/login-1a2b3c", {"login.py": "def login():\n    return True\n"}, "Add login")
    text, truncated = gitops.diff(str(repo), base, "orc/durg/login-1a2b3c")
    assert not truncated
    assert "diff --git a/login.py b/login.py" in text
    assert "+    return True" in text
    assert "work in progress" not in text and "notes.txt" not in text

    cut, truncated = gitops.diff(str(repo), base, "orc/durg/login-1a2b3c", limit=40)
    assert truncated and len(cut) <= 40 and text.startswith(cut)


def test_diff_of_a_bundle_needs_no_repo(repo, tmp_path):
    base = git(repo, "rev-parse", "main")
    tip = commit_on(repo, "orc/durg/login-1a2b3c", {"login.py": "x\n"}, "Add login")
    bundle = tmp_path / "work.bundle"
    git(repo, "bundle", "create", str(bundle), "orc/durg/login-1a2b3c")
    text, _ = gitops.diff_bundle(bundle, base, tip)
    assert "+x" in text


def test_head_branch_and_detached_head(repo):
    assert gitops.head_branch(str(repo)) == "main"
    git(repo, "checkout", "-q", "--detach")
    assert gitops.head_branch(str(repo)) is None
    assert gitops.bundle_head(str(repo), repo.parent / "b" / "head.bundle") is None


def test_merges_even_when_git_has_no_identity(repo, monkeypatch):
    git(repo, "branch", "develop")
    tip = commit_on(repo, "orc/durg/login-1a2b3c", {"login.py": "x = 1\n"}, "Add login", start="develop")
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")  # never guess an identity from the hostname
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.useConfigOnly")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")

    result = gitops.merge(str(repo), "orc/durg/login-1a2b3c", "develop", expect=tip)

    assert result.status == "merged"
    assert git(repo, "log", "-1", "--format=%an <%ae>", "develop") == "orc <orc@localhost>"


def test_head_branch_ignores_a_tag_with_the_same_name(repo):
    git(repo, "tag", "main")  # `symbolic-ref --short` would answer "heads/main"
    assert gitops.head_branch(str(repo)) == "main"
    git(repo, "checkout", "-q", "--detach")
    assert gitops.head_branch(str(repo)) is None
