"""Authoritative board state on the server.

Events are plain dicts shaped exactly like the `OrcEvent` union in
`web/src/types.ts`, so the desktop app can apply them unchanged.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
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


class Board:
    def __init__(self) -> None:
        self.orcs: dict[str, Orc] = {}
        self.edges: list[Edge] = []
        self.gold = 0
        self.repo: str | None = None

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

    def snapshot(self) -> list[dict[str, Any]]:
        """Events that rebuild this board from scratch on a fresh client."""
        events: list[dict[str, Any]] = [{"type": "board.reset"}, {"type": "board.repo", "repo": self.repo}]
        events += [{"type": "orc.spawned", "orc": o.to_dict()} for o in self.orcs.values()]
        events += [{"type": "edge.added", "edge": e.to_dict()} for e in self.edges]
        if self.gold:
            events.append({"type": "gold.banked", "amount": self.gold})
        return events
