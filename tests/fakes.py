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
        self.killed = 0
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

    async def kill_processes(self) -> None:
        self.killed += 1  # the real thing signals the box's processes; here they are host processes

    async def destroy(self) -> None:
        self.destroyed = True
        shutil.rmtree(self.root, ignore_errors=True)


class LocalTransport:
    """Stands in for ssh: runs the remote command on this machine, and records what was run.

    The command goes through the same quoting as production (`gitops.remote_command`) and a real
    `sh -c`, so a word that would be interpreted by a remote shell is interpreted here too.
    `down` makes every call fail like an unreachable host; `fail_on` fails commands whose argv
    contains one of the given words (a transport that dies half way).
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.down = False
        self.fail_on: set[str] = set()

    def run(self, argv, *, env=None, stdin=None, stdout=None, max_bytes=None):
        import subprocess

        from orc import gitops

        self.calls.append(list(argv))
        if self.down or self.fail_on & set(argv):
            return subprocess.CompletedProcess(argv, 255, "", "ssh: connect to host fakehost port 22: Connection refused")
        command = gitops.remote_command(argv, env)
        return gitops.spawn(["sh", "-c", command], stdin=stdin, stdout=stdout, max_bytes=max_bytes, timeout=60)

    def ran(self, word: str) -> list[list[str]]:
        return [c for c in self.calls if word in c]
