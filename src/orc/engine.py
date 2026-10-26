"""The real engine: runs orders through black boxes and narrates every step
as board events.

Each orc has its own job queue and worker coroutine. Jobs:
- plan   (chieftain): read the repo, split the job across connected workers
- code   (coder/tester): work in a box on a fresh branch, export it
- review (reviewer): read the branch, approve or send findings back
Approved branches are fetched into the user's repo as `orc/...` branches.
"""

from __future__ import annotations

import asyncio
import math
import random
import re
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orc import gitops, prompts
from orc.board import Board, Orc
from orc.runtimes import Runtime
from orc.sandbox import WORKDIR, Sandbox

WALK_S = 3.2
FLIGHT_S = 0.9
MAX_FIX_ROUNDS = 2

NAMES = [
    "Durg", "Mogka", "Brakk", "Uzgash", "Rokhar", "Gruk", "Nazka", "Throg",
    "Borka", "Skarn", "Ulgra", "Dakka", "Morz", "Grisha", "Kruul", "Yarga",
]
VOICES = ["Work work!", "Zug zug.", "Me on it.", "Ugh... fine.", "Yes, chief!", "Smash code!"]
ROLES = ("chieftain", "coder", "reviewer", "tester")
BACKENDS = ("claude", "cursor", "copilot", "codex")

_KEEP = object()  # "leave the orc's task unchanged"

Emit = Callable[[dict[str, Any]], None]
SandboxFactory = Callable[[str], Awaitable[Sandbox]]


class Abort(Exception):
    """Stop a job quietly (already explained in the log)."""


@dataclass
class Job:
    kind: str  # plan | code | review
    task: str
    source: str | None = None
    attempt: int = 0
    work_id: str | None = None
    findings: list[str] = field(default_factory=list)


@dataclass
class Work:
    """One branch of work: owned by a coder, kept in its box until delivered."""

    id: str
    owner: str
    task: str
    branch: str
    base: str = ""
    box: Sandbox | None = None
    ready: bool = False
    bundle: Path | None = None


def slot(i: int) -> dict[str, int]:
    """Hut positions: inner ring of 6 around the town hall, then an outer ring of 10."""
    if i < 6:
        a = -math.pi / 2 + math.pi / 6 + i * math.pi * 2 / 6
        return {"x": round(math.cos(a) * 300), "y": round(math.sin(a) * 230)}
    a = (i - 6) * math.pi * 2 / 10
    return {"x": round(math.cos(a) * 540), "y": round(math.sin(a) * 410)}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower())[:24].strip("-") or "task"


class Engine:
    def __init__(
        self,
        emit: Emit,
        *,
        sandbox_factory: SandboxFactory,
        runtime_for: Callable[[str], Runtime],
        walk_s: float = WALK_S,
        flight_s: float = FLIGHT_S,
        rng: random.Random | None = None,
    ) -> None:
        self.board = Board()
        self._out = emit
        self.sandbox_factory = sandbox_factory
        self.runtime_for = runtime_for
        self.walk_s = walk_s
        self.flight_s = flight_s
        self.rng = rng or random.Random()
        self.queues: dict[str, asyncio.Queue[Job]] = {}
        self.workers: dict[str, asyncio.Task[None]] = {}
        self.gates: dict[str, asyncio.Future[bool]] = {}
        self.decisions: dict[str, asyncio.Future[bool]] = {}
        self.works: dict[str, Work] = {}
        self._seq = 0
        self._slots = 0

    # --- lifecycle ---

    def boot(self) -> None:
        if self.board.orcs:
            return
        self.spawn("chieftain", "claude")
        self.spawn("coder", "claude")
        self.spawn("coder", "claude")
        self.spawn("reviewer", "claude")

    async def shutdown(self) -> None:
        for task in self.workers.values():
            task.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)
        for work in list(self.works.values()):
            if work.box:
                await work.box.destroy()

    async def handle(self, op: dict[str, Any]) -> None:
        """Apply one command from a client."""
        kind = op.get("op")
        oid = str(op.get("id", ""))
        if kind == "spawn" and op.get("role") in ROLES and op.get("backend") in BACKENDS:
            self.spawn(op["role"], op["backend"])
        elif kind == "command":
            self.command([str(i) for i in op.get("ids", [])], str(op.get("text", "")))
        elif kind == "approve":
            self._settle(self.gates, oid, True)
        elif kind == "reject":
            self._settle(self.gates, oid, False)
        elif kind == "retry":
            self._settle(self.decisions, oid, True)
        elif kind == "abandon":
            self._settle(self.decisions, oid, False)
        elif kind == "setAutonomy" and oid in self.board.orcs:
            self.set_autonomy(oid, bool(op.get("on")))
        elif kind == "setRepo":
            self.set_repo(str(op.get("repo", "")))

    # --- player actions ---

    def spawn(self, role: str, backend: str) -> str | None:
        if role == "chieftain" and self._hall():
            return None
        self._seq += 1
        oid = f"orc-{self._seq}"
        used = {o.name for o in self.board.orcs.values()}
        name = "Ozgar" if role == "chieftain" else next((n for n in NAMES if n not in used), f"Peon {self._seq}")
        hut = {"x": 0, "y": 0} if role == "chieftain" else slot(self._slots)
        if role != "chieftain":
            self._slots += 1
        orc = Orc(id=oid, name=name, role=role, backend=backend, autonomy=role != "chieftain", hut=hut)
        self._emit("orc.spawned", orc=orc.to_dict())

        hall = self._hall()
        if role != "chieftain" and hall:
            self._link(hall.id, oid, "command")
        if role in ("coder", "tester"):
            for r in self._by_role("reviewer"):
                self._link(oid, r.id, "review")
        if role == "reviewer":
            for c in self._by_role("coder") + self._by_role("tester"):
                self._link(c.id, oid, "review")

        self.queues[oid] = asyncio.Queue()
        self.workers[oid] = asyncio.get_running_loop().create_task(self._worker(oid))
        self._log(oid, f"{name} the {role} reporting for duty ({backend}).")
        self._say(oid, "Ready to work!")
        return oid

    def command(self, ids: list[str], text: str) -> None:
        text = text.strip()
        if not text:
            return
        for oid in ids:
            orc = self.board.orcs.get(oid)
            if not orc:
                continue
            if orc.role == "reviewer":
                self._log(oid, "Me only review what coders bring. Give orders to the chieftain or a coder.", "warn")
                continue
            self._say(oid, self.rng.choice(VOICES))
            self._enqueue(oid, Job("plan" if orc.role == "chieftain" else "code", text))

    def set_autonomy(self, oid: str, on: bool) -> None:
        self._emit("orc.autonomy", id=oid, autonomy=on)
        self._log(oid, "Autonomy on: acting without asking." if on else "Autonomy off: will ask before acting.")
        if on:
            self._settle(self.gates, oid, True)

    def set_repo(self, repo: str) -> None:
        hall = self._hall()
        try:
            resolved = gitops.validate_repo(repo)
        except ValueError as exc:
            if hall:
                self._log(hall.id, f"Bad war target: {exc}", "error")
            return
        self._emit("board.repo", repo=resolved)
        if hall:
            self._log(hall.id, f"War target set: {resolved}", "ok")
            self._say(hall.id, "To war!")

    # --- job plumbing ---

    def _enqueue(self, oid: str, job: Job) -> None:
        orc = self.board.orcs.get(oid)
        queue = self.queues.get(oid)
        if not orc or queue is None:
            return
        if orc.state != "idle" or not queue.empty():
            self._log(oid, f"Queued: {job.task}")
        queue.put_nowait(job)

    async def _worker(self, oid: str) -> None:
        queue = self.queues[oid]
        while True:
            job = await queue.get()
            await self._run_with_retries(oid, job)
            if queue.empty():
                self._state(oid, "idle", None)

    async def _run_with_retries(self, oid: str, job: Job) -> None:
        flow = {"plan": self._plan, "code": self._code, "review": self._review}[job.kind]
        while True:
            try:
                await flow(oid, job)
                return
            except asyncio.CancelledError:
                raise
            except Abort:
                return
            except Exception as exc:  # any failure parks the orc until the player decides
                self._state(oid, "failed")
                self._log(oid, str(exc) or exc.__class__.__name__, "error")
                self._say(oid, "Ugh. It broke.")
                if await self._decide(oid):
                    self._log(oid, "Trying again.")
                    continue
                await self._drop_work(job)
                self._log(oid, "Task abandoned. Black box destroyed.", "warn")
                return

    async def _gate(self, oid: str, pending: dict[str, Any]) -> bool:
        orc = self.board.orcs[oid]
        if orc.autonomy:
            return True
        self._log(oid, f"Awaiting your orders: {pending['summary']}.", "warn")
        self._emit("orc.awaiting", id=oid, pending=pending)
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.gates[oid] = fut
        try:
            ok = await fut
        finally:
            self.gates.pop(oid, None)
        self._emit("orc.resolved", id=oid)
        if ok:
            self._log(oid, "Orders approved.", "ok")
            self._say(oid, "Zug zug.")
        else:
            self._log(oid, "Orders rejected. Standing down.", "warn")
        return ok

    async def _decide(self, oid: str) -> bool:
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.decisions[oid] = fut
        try:
            return await fut
        finally:
            self.decisions.pop(oid, None)

    @staticmethod
    def _settle(futures: dict[str, asyncio.Future[bool]], oid: str, value: bool) -> None:
        fut = futures.get(oid)
        if fut and not fut.done():
            fut.set_result(value)

    # --- flows ---

    async def _plan(self, oid: str, job: Job) -> None:
        orc = self.board.orcs[oid]
        self._state(oid, "thinking", job.task)
        self._log(oid, f'Studying the war map: "{job.task}"')
        repo = self._require_repo(oid)
        workers = [o for o in self._command_targets(oid) if o.role in ("coder", "tester")]
        if not workers:
            self._log(oid, "No peons to command. Train some coders first.", "warn")
            raise Abort

        runtime = self.runtime_for(orc.backend)
        async with self._box(f"{orc.name}-plan") as box:
            self._log(oid, "Reading the repo in a black box...")
            await self._clone_repo(box, repo)
            reply = await runtime.run(
                box,
                prompts.plan(job.task, [(w.name, w.role) for w in workers]),
                self._logger(oid),
                kind="plan",
                hints={"task": job.task, "workers": [w.name for w in workers]},
            )
        by_name = {w.name: w for w in workers}
        orders = [(by_name[n], t) for n, t in prompts.parse_plan(reply, list(by_name))]

        pending = {
            "kind": "plan",
            "summary": f"Split the job into {len(orders)} tasks",
            "items": [f"{w.name} ({w.role}): {t}" for w, t in orders],
        }
        if not await self._gate(oid, pending):
            raise Abort
        self._state(oid, "working", job.task)
        loop = asyncio.get_running_loop()
        for w, task in orders:
            self._emit("orders.sent", **{"from": oid, "to": w.id, "label": task, "durationMs": int(self.flight_s * 1000)})
            loop.call_later(self.flight_s, self._receive_order, w.id, task, oid)
            await asyncio.sleep(self.flight_s * 0.4)
        await asyncio.sleep(self.flight_s)
        self._log(oid, f"Orders delivered to {len(orders)} peons.", "ok")

    def _receive_order(self, oid: str, task: str, source: str) -> None:
        self._say(oid, self.rng.choice(VOICES))
        self._enqueue(oid, Job("code", task, source=source))

    async def _code(self, oid: str, job: Job) -> None:
        orc = self.board.orcs[oid]
        repo = self._require_repo(oid)
        work = self.works.get(job.work_id or "")
        fixing = work is not None and bool(job.findings)
        title = f"Fix review findings: {job.task}" if fixing else job.task
        self._state(oid, "thinking", title)

        if work is None:
            wid = secrets.token_hex(3)
            work = Work(id=wid, owner=oid, task=job.task, branch=f"orc/{slug(orc.name)}/{slug(job.task)}-{wid}")
            self.works[wid] = work
            job.work_id = wid

        pending = {
            "kind": "commands",
            "summary": "Address the review in the black box" if fixing else "Work in a fresh black box",
            "items": [
                "docker: orc-peon box, no host mounts, 4 GB / 2 CPU",
                f"git clone {repo} -> branch {work.branch}",
                f'{orc.backend} -p "{title}" (full permissions inside the box)',
                "commit, export branch as a git bundle",
            ],
        }
        if not await self._gate(oid, pending):
            await self._drop_work(job)
            raise Abort

        self._state(oid, "working", title)
        if work.box and not work.ready:  # a failed earlier attempt left a half-built box
            await work.box.destroy()
            work.box = None
        if work.box is None:
            self._log(oid, "Raising a black box...")
            work.box = await self.sandbox_factory(f"{orc.name}-{work.id}")
            work.base = await self._clone_repo(work.box, repo, branch=work.branch)
            work.ready = True
            self._log(oid, f"Cloned the repo at {work.base[:8]} onto {work.branch}.")

        prompt = prompts.fix(orc.name, job.task, job.findings) if fixing else prompts.code(orc.name, orc.role, job.task)
        summary = await self.runtime_for(orc.backend).run(
            work.box, prompt, self._logger(oid), kind="code", hints={"task": job.task}
        )
        # Runtimes stream the agent's messages already; only add the summary if it wasn't shown.
        last = summary.strip().splitlines()[-1][:200] if summary.strip() else ""
        recent = [line["text"].rstrip("…")[:60] for line in orc.log[-5:]]
        if last and last[:60] not in recent and not any(last.startswith(r) for r in recent if r):
            self._log(oid, last)

        commits = await self._commit_all(work.box, work, title)
        if commits == 0:
            raise RuntimeError("No changes were made, so there is nothing to hand over.")
        _, stat = await work.box.exec(["git", "diff", "--shortstat", f"{work.base}..HEAD"])
        work.bundle = gitops.cache_dir() / "bundles" / f"{work.id}.bundle"
        await work.box.exec(["git", "bundle", "create", "/tmp/out.bundle", work.branch])
        await work.box.copy_out("/tmp/out.bundle", work.bundle)
        self._log(oid, f"Exported {work.branch} ({stat.strip() or f'{commits} commits'}).", "ok")

        reviewer = self._reviewer_for(oid)
        if reviewer is None:
            await self._deliver(oid, work)
            return
        self._state(oid, "walking", title)
        self._emit("crate.sent", **{"from": oid, "to": reviewer.id, "label": "branch", "durationMs": int(self.walk_s * 1000)})
        review = Job("review", job.task, source=oid, attempt=job.attempt, work_id=work.id)
        asyncio.get_running_loop().call_later(self.walk_s / 2, self._enqueue, reviewer.id, review)
        await asyncio.sleep(self.walk_s)

    async def _review(self, oid: str, job: Job) -> None:
        orc = self.board.orcs[oid]
        work = self.works.get(job.work_id or "")
        if work is None or work.bundle is None:
            raise Abort
        author = self.board.orcs[work.owner]
        self._state(oid, "reviewing", f"Review: {job.task}")
        self._log(oid, f"Reading {author.name}'s branch {work.branch}...")

        async with self._box(f"{orc.name}-review") as box:
            await box.copy_in(work.bundle, "/tmp/work.bundle")
            await box.exec(["git", "clone", "-q", "-b", work.branch, "/tmp/work.bundle", WORKDIR], workdir=None)
            reply = await self.runtime_for(orc.backend).run(
                box,
                prompts.review(job.task, work.base),
                self._logger(oid),
                kind="review",
                hints={"attempt": job.attempt},
            )
        verdict = prompts.parse_review(reply)

        if not verdict.approve and job.attempt < MAX_FIX_ROUNDS:
            self._log(oid, "Changes requested: " + "; ".join(verdict.findings or ["(no details)"]), "warn")
            self._state(oid, "walking")
            self._emit("crate.sent", **{"from": oid, "to": author.id, "label": "findings", "durationMs": int(self.walk_s * 1000)})
            fix = Job("code", job.task, source=oid, attempt=job.attempt + 1, work_id=work.id, findings=verdict.findings)
            asyncio.get_running_loop().call_later(self.walk_s / 2, self._enqueue, author.id, fix)
            await asyncio.sleep(self.walk_s)
            return
        if not verdict.approve:
            self._log(oid, f"Still unhappy after {MAX_FIX_ROUNDS} rounds; the chief decides. " + "; ".join(verdict.findings), "warn")
        else:
            self._log(oid, f"Approved {author.name}'s work. Hauling gold to the town hall.", "ok")
        await self._deliver(oid, work)

    async def _deliver(self, oid: str, work: Work) -> None:
        """Fetch the finished branch into the user's repo: the gold."""
        repo = self.board.repo or ""
        hall = self._hall()
        if work.bundle and not gitops.is_url(repo):
            await asyncio.to_thread(gitops.fetch_branch, repo, work.bundle, work.branch)
            where = f"Branch {work.branch} is in your repo. Merge when ready."
        else:
            where = f"Branch {work.branch} is bundled at {work.bundle} (remote repos can't be fetched into)."
        self._log(work.owner, where, "ok")
        if hall:
            self._log(hall.id, f"Gold! {where}", "ok")

        self._state(oid, "walking")
        if hall:
            self._emit("gold.delivered", **{"from": oid, "to": hall.id, "durationMs": int(self.walk_s * 1000)})
        asyncio.get_running_loop().call_later(self.walk_s / 2, lambda: self._emit("gold.banked", amount=1))
        if work.box:
            await work.box.destroy()
        self.works.pop(work.id, None)
        await asyncio.sleep(self.walk_s)

    async def _drop_work(self, job: Job) -> None:
        work = self.works.pop(job.work_id or "", None)
        if work and work.box:
            await work.box.destroy()

    # --- box helpers ---

    @asynccontextmanager
    async def _box(self, label: str):
        box = await self.sandbox_factory(label)
        try:
            yield box
        finally:
            await box.destroy()

    async def _clone_repo(self, box: Sandbox, repo: str, branch: str | None = None) -> str:
        """Clone the war target into the box; returns the base commit."""
        if gitops.is_url(repo):
            await box.exec(["git", "clone", "-q", repo, WORKDIR], workdir=None)
        else:
            bundle = gitops.cache_dir() / "bundles" / f"head-{secrets.token_hex(4)}.bundle"
            await asyncio.to_thread(gitops.bundle_head, repo, bundle)
            try:
                await box.copy_in(bundle, "/tmp/repo.bundle")
            finally:
                bundle.unlink(missing_ok=True)
            await box.exec(["git", "clone", "-q", "/tmp/repo.bundle", WORKDIR], workdir=None)
        _, base = await box.exec(["git", "rev-parse", "HEAD"])
        if branch:
            await box.exec(["git", "checkout", "-q", "-b", branch])
        return base.strip()

    async def _commit_all(self, box: Sandbox, work: Work, message: str) -> int:
        await box.exec(["sh", "-c", 'git add -A && (git diff --cached --quiet || git commit -q -m "$1")', "sh", message[:72]])
        _, count = await box.exec(["git", "rev-list", "--count", f"{work.base}..HEAD"])
        return int(count.strip() or 0)

    # --- lookups and event helpers ---

    def _require_repo(self, oid: str) -> str:
        if not self.board.repo:
            self._log(oid, "No war target. Select the chieftain and set the repo path first.", "warn")
            raise Abort
        return self.board.repo

    def _hall(self) -> Orc | None:
        return next(iter(self._by_role("chieftain")), None)

    def _by_role(self, role: str) -> list[Orc]:
        return [o for o in self.board.orcs.values() if o.role == role]

    def _command_targets(self, oid: str) -> list[Orc]:
        return [self.board.orcs[e.target] for e in self.board.edges if e.source == oid and e.kind == "command"]

    def _reviewer_for(self, oid: str) -> Orc | None:
        reviewers = [self.board.orcs[e.target] for e in self.board.edges if e.source == oid and e.kind == "review"]

        def load(o: Orc) -> int:
            return self.queues[o.id].qsize() + (0 if o.state == "idle" else 1)

        return min(reviewers, key=load, default=None)

    def _link(self, source: str, target: str, kind: str) -> None:
        if not any(e.source == source and e.target == target and e.kind == kind for e in self.board.edges):
            self._emit("edge.added", edge={"from": source, "to": target, "kind": kind})

    def _logger(self, oid: str) -> Callable[[str, str], None]:
        return lambda text, level="info": self._log(oid, text, level)

    def _emit(self, type_: str, **fields: Any) -> None:
        event = {"type": type_, **fields}
        self.board.apply(event)
        self._out(event)

    def _state(self, oid: str, state: str, task: Any = _KEEP) -> None:
        if task is _KEEP:
            self._emit("orc.state", id=oid, state=state)
        else:
            self._emit("orc.state", id=oid, state=state, task=task)

    def _log(self, oid: str, line: str, level: str = "info") -> None:
        self._emit("orc.log", id=oid, line=line, level=level)

    def _say(self, oid: str, text: str) -> None:
        self._emit("orc.say", id=oid, text=text)
