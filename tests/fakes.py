"""Test doubles: a "box" that is just a temp directory on this machine.

Commands run for real (git included), with the box's /home/node/work and
/tmp mapped into the directory, so engine flows can be tested without Docker.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from orc.sandbox import WORKDIR, SandboxError


class LocalSandbox:
    created: list["LocalSandbox"] = []

    def __init__(self, name: str) -> None:
        self.name = name
        self.root = Path(tempfile.mkdtemp(prefix="orc-box-"))
        (self.root / "tmp").mkdir()
        self.destroyed = False
        LocalSandbox.created.append(self)

    @classmethod
    async def create(cls, label: str) -> "LocalSandbox":
        return cls(label)

    def _map(self, path: str) -> str:
        if path.startswith(WORKDIR):
            return str(self.root / "work") + path[len(WORKDIR):]
        if path.startswith("/tmp/"):
            return str(self.root / "tmp" / path[len("/tmp/"):])
        return path

    async def exec(
        self,
        argv: list[str],
        *,
        env_names: tuple[str, ...] = (),
        on_line: Callable[[str], None] | None = None,
        check: bool = True,
        workdir: str | None = WORKDIR,
    ) -> tuple[int, str]:
        cwd = self._map(workdir) if workdir else str(self.root)
        proc = await asyncio.create_subprocess_exec(
            *[self._map(a) for a in argv],
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        text = out.decode().strip()
        if on_line:
            for line in text.splitlines():
                on_line(line)
        if check and proc.returncode:
            raise SandboxError(f"{argv[:3]} failed: {text}")
        return proc.returncode or 0, text

    async def copy_in(self, host_path: Path, dest: str) -> None:
        shutil.copy(host_path, self._map(dest))

    async def copy_out(self, src: str, host_path: Path) -> None:
        host_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self._map(src), host_path)

    async def destroy(self) -> None:
        self.destroyed = True
        shutil.rmtree(self.root, ignore_errors=True)
