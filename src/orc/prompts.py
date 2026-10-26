"""Prompts for each role and parsers for the structured answers they return."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def plan(task: str, workers: list[tuple[str, str]]) -> str:
    roster = "\n".join(f"- {name} ({role})" for name, role in workers)
    return f"""You are the chieftain of a team of coding agents working on the repository in the current directory.

Job from the user: {task}

Your workers:
{roster}

Look at the repository as much as you need, then split the job into one task per worker.
Coders implement; testers write or improve tests. Tasks must be independent enough to be
done in parallel on separate branches. Keep each task to one or two sentences.

Reply with ONLY a JSON object, no other text:
{{"tasks": [{{"worker": "<worker name>", "task": "<what they must do>"}}]}}"""


def code(name: str, role: str, task: str) -> str:
    focus = "Write or improve tests for this." if role == "tester" else "Implement this."
    return f"""You are {name}, a {role} on a team of coding agents. You are working in the repository in the
current directory, on a branch created for you.

Task: {task}

{focus} Keep the change focused on the task. Run the project's tests or build if there are any and
they are quick. When you are done, commit your work with git (a clear one-line message). Do not push.
Finish with a two-sentence summary of what you changed."""


def fix(name: str, task: str, findings: list[str]) -> str:
    notes = "\n".join(f"- {f}" for f in findings) or "- (no details given)"
    return f"""You are {name}. The reviewer sent back your work on this task:

Task: {task}

Review findings:
{notes}

Address every finding in the repository in the current directory (you are on your branch). Run quick
tests if any. Commit with git when done. Do not push. Finish with a one-sentence summary."""


def review(task: str, base: str) -> str:
    return f"""You are a code reviewer. The repository in the current directory is on a branch that was
written for this task:

Task: {task}

Review the change with `git diff {base}...HEAD` (and read surrounding code as needed). Check that it does
what the task asks, is correct, and does not break anything obvious. Do not modify any files.

Reply with ONLY a JSON object, no other text:
{{"approve": true or false, "findings": ["<specific problem to fix>", ...]}}
Approve unless there is a real problem; findings must be empty when approving."""


def extract_json(text: str) -> Any:
    """Parse the JSON object in a model reply, tolerating fences or chatter around it."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"No JSON object in reply: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def parse_plan(text: str, worker_names: list[str]) -> list[tuple[str, str]]:
    data = extract_json(text)
    known = {n.lower(): n for n in worker_names}
    out: list[tuple[str, str]] = []
    for item in data.get("tasks", []):
        name = known.get(str(item.get("worker", "")).strip().lower())
        task = str(item.get("task", "")).strip()
        if name and task:
            out.append((name, task))
    if not out:
        raise ValueError("The plan assigned no tasks to known workers.")
    return out


@dataclass
class Verdict:
    approve: bool
    findings: list[str]


def parse_review(text: str) -> Verdict:
    data = extract_json(text)
    findings = [str(f) for f in data.get("findings", []) if str(f).strip()]
    return Verdict(approve=bool(data.get("approve")), findings=findings)
