"""Authoritative board state on the server.

Events are plain dicts shaped exactly like the `OrcEvent` union in
`web/src/types.ts`, so the desktop app can apply them unchanged.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any

LOG_LIMIT = 200


@dataclass
class Orc:
    id: str
    name: str
    role: str
    backend: str
    autonomy: bool
    hut: dict[str, int]
    state: str = "idle"
    task: str | None = None
    log: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Edge:
    source: str
    target: str
    kind: str  # "command" | "review"

    def to_dict(self) -> dict[str, str]:
        return {"from": self.source, "to": self.target, "kind": self.kind}


DELIVERY_STATUSES = ("delivered", "bundled", "merged", "conflict", "missing", "moved")
_WIRE = {"base_branch": "baseBranch", "merge_commit": "mergeCommit"}


@dataclass
class Delivery:
    """A finished branch: the gold. Lives in the user's repo (or a bundle, for URL targets).

    status: `delivered` (in the repo, unmerged), `bundled` (URL target: only a
    bundle exists), `merged`, `conflict` (an in-app merge found conflicts),
    `missing` (deleted outside orc), `moved` (its tip changed outside orc).
    `command` is a git command for the player to run themselves, when orc won't.
    """

    id: str
    branch: str
    task: str
    owner: str
    repo: str
    base: str  # commit the work started from
    base_branch: str | None  # branch HEAD was on then: the merge target (None: detached)
    head: str  # commit delivered
    stat: str
    status: str = "delivered"
    note: str | None = None
    command: str | None = None
    conflicts: list[str] = field(default_factory=list)
    merge_commit: str | None = None
    bundle: str | None = None
    at: int = 0  # ms since epoch

    def to_dict(self) -> dict[str, Any]:
        return {_WIRE.get(k, k): v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Delivery":
        back = {v: k for k, v in _WIRE.items()}
        known = {f.name for f in fields(cls)}
        values = {back.get(k, k): v for k, v in data.items()}
        return cls(**{k: v for k, v in values.items() if k in known})


class Board:
    def __init__(self) -> None:
        self.orcs: dict[str, Orc] = {}
        self.edges: list[Edge] = []
        self.gold = 0
        self.repo: str | None = None
        self.deliveries: dict[str, Delivery] = {}

    def apply(self, e: dict[str, Any]) -> None:
        kind = e["type"]
        orc = self.orcs.get(e.get("id", ""))
        if kind == "orc.spawned":
            data = e["orc"]
            self.orcs[data["id"]] = Orc(**data)
        elif kind == "edge.added":
            edge = e["edge"]
            self.edges.append(Edge(edge["from"], edge["to"], edge["kind"]))
        elif kind == "orc.state" and orc:
            orc.state = e["state"]
            if "task" in e:
                orc.task = e["task"]
        elif kind == "orc.log" and orc:
            orc.log.append({"t": int(time.time() * 1000), "text": e["line"], "level": e.get("level", "info")})
            del orc.log[:-LOG_LIMIT]
        elif kind == "orc.awaiting" and orc:
            orc.state = "awaiting"
            orc.pending = e["pending"]
        elif kind == "orc.resolved" and orc:
            orc.pending = None
        elif kind == "orc.autonomy" and orc:
            orc.autonomy = e["autonomy"]
        elif kind == "gold.banked":
            self.gold += e["amount"]
        elif kind == "board.repo":
            self.repo = e["repo"]
        elif kind == "branch.delivered":
            d = Delivery.from_dict(e["delivery"])
            self.deliveries[d.id] = d
        elif kind == "branch.status" and (d := self.deliveries.get(e["id"])):
            d.status = e["status"]
            d.note = e.get("note")
            d.command = e.get("command")
            d.conflicts = list(e.get("conflicts", []))
        elif kind == "branch.merged" and (d := self.deliveries.get(e["id"])):
            d.status, d.merge_commit = "merged", e["commit"]
            d.note, d.command, d.conflicts = None, None, []

    def snapshot(self) -> list[dict[str, Any]]:
        """Events that rebuild this board from scratch on a fresh client."""
        events: list[dict[str, Any]] = [{"type": "board.reset"}, {"type": "board.repo", "repo": self.repo}]
        events += [{"type": "orc.spawned", "orc": o.to_dict()} for o in self.orcs.values()]
        events += [{"type": "edge.added", "edge": e.to_dict()} for e in self.edges]
        if self.gold:
            events.append({"type": "gold.banked", "amount": self.gold})
        events += [{"type": "branch.delivered", "delivery": d.to_dict()} for d in self.deliveries.values()]
        return events
