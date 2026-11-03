"""Keeps the village between launches: units, links, logs, gold, delivered branches
and the war target.

Only what the board knows is saved. Jobs, gates and black boxes end with the
engine (boxes are destroyed on shutdown), so a restored orc always wakes up idle.
The file can hold agent output from the logs, so it is private to the user.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from orc.board import DELIVERY_STATUSES, Board, Delivery, Edge, Orc

VERSION = 1


def state_path() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "orc" / "board.json"


@dataclass
class Saved:
    orcs: list[Orc]
    edges: list[Edge]
    gold: int
    repo: str | None
    deliveries: list[Delivery] = field(default_factory=list)


class BoardStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or state_path()

    def load(self) -> Saved | None:
        """The saved village, or None if there is none.

        An unreadable file is moved aside to `board.json.bad` (so the next save
        can't destroy it) and reported as ValueError.
        """
        if not self.path.exists():
            return None
        try:
            return _parse(json.loads(self.path.read_text()))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            bad = self.path.with_name(self.path.name + ".bad")
            try:
                self.path.replace(bad)
            except OSError:
                pass  # can't keep it aside; still start a fresh village rather than fail to boot
            raise ValueError(f"Couldn't read the saved village ({exc}); moved it to {bad}.") from exc

    def save(self, board: Board) -> None:
        data = {
            "version": VERSION,
            "repo": board.repo,
            "gold": board.gold,
            "orcs": [o.to_dict() for o in board.orcs.values()],
            "edges": [e.to_dict() for e in board.edges],
            "deliveries": [d.to_dict() for d in board.deliveries.values()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)  # atomic: a crash mid-write leaves the old file intact


def _parse(data: Any) -> Saved:
    if not isinstance(data, dict):
        raise ValueError("not a village file")
    if data.get("version") != VERSION:
        raise ValueError(f"unknown version {data.get('version')!r}")
    known = {f.name for f in fields(Orc)}
    orcs = []
    for raw in data["orcs"]:
        if not isinstance(raw, dict) or not {"id", "name", "role", "backend", "autonomy", "hut"} <= raw.keys():
            raise ValueError("bad orc entry")
        orcs.append(Orc(**{k: v for k, v in raw.items() if k in known}))
    ids = {o.id for o in orcs}
    edges = [Edge(e["from"], e["to"], e["kind"]) for e in data["edges"]]
    if any(e.source not in ids or e.target not in ids for e in edges):
        raise ValueError("edge to an unknown orc")
    deliveries = []
    for raw in data.get("deliveries", []):  # absent in saves from before in-app merging
        required = {"id", "branch", "task", "owner", "repo", "base", "baseBranch", "head", "stat"}
        if not isinstance(raw, dict) or not required <= raw.keys() or raw.get("status", "delivered") not in DELIVERY_STATUSES:
            raise ValueError("bad delivery entry")
        deliveries.append(Delivery.from_dict(raw))
    repo = data.get("repo")
    return Saved(
        orcs=orcs,
        edges=edges,
        gold=int(data.get("gold", 0)),
        repo=repo if isinstance(repo, str) else None,
        deliveries=deliveries,
    )
