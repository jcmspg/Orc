"""Black boxes: one disposable Docker container per job.

Nothing from the host is mounted. Code goes in and out only through
`docker cp` of git bundles, so an agent with full permissions inside the box
can break the box but never the user's machine.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from pathlib import Path
from typing import Any, Callable, Protocol

IMAGE = "orc-peon:latest"
WORKDIR = "/home/node/work"
DOCKERFILE_DIR = Path(__file__).parent / "docker"
STREAM_LIMIT = 16 * 1024 * 1024  # agent stream-json lines can be large


class SandboxError(RuntimeError):
    pass


class Sandbox(Protocol):
    name: str

    async def exec(
        self,
        argv: list[str],
        *,
        env_names: tuple[str, ...] = (),
        on_line: Callable[[str], None] | None = None,
        check: bool = True,
        workdir: str | None = WORKDIR,
    ) -> tuple[int, str]: ...

    async def copy_in(self, host_path: Path, dest: str) -> None: ...

    async def copy_out(self, src: str, host_path: Path) -> None: ...

    async def kill_processes(self) -> None:
        """Stop everything running in the box, keeping the box and its files."""
        ...

    async def destroy(self) -> None: ...


DOCKER_MISSING = (
    "Docker isn't installed (or isn't on PATH). orc runs its agents in Docker black boxes: "
    "install Docker Desktop (Windows/macOS) or Docker Engine (Linux), start it, and try again."
)


async def _spawn(*cmd: str, **kwargs: Any) -> asyncio.subprocess.Process:
    try:
        return await asyncio.create_subprocess_exec(*cmd, **kwargs)
    except FileNotFoundError:
        raise SandboxError(DOCKER_MISSING) from None


async def _run(*cmd: str) -> str:
    proc = await _spawn(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    text = out.decode(errors="replace").strip()
    if proc.returncode != 0:
        raise SandboxError(f"{' '.join(cmd[:3])} failed ({proc.returncode}): {_tail(text)}")
    return text


def _tail(text: str, lines: int = 6) -> str:
    return "\n".join(text.splitlines()[-lines:])


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:24] or "box"


class DockerSandbox:
    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    async def create(cls, label: str, image: str = IMAGE) -> "DockerSandbox":
        await ensure_image(image)
        name = f"orc-{_slug(label)}-{secrets.token_hex(3)}"
        run = asyncio.ensure_future(_run(
            "docker", "run", "-d",
            "--name", name,
            "--label", "orc=peon",
            "--memory", "4g",
            "--cpus", "2",
            "--pids-limit", "1024",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            image,
            "sleep", "infinity",
        ))
        try:
            await asyncio.shield(run)
        except asyncio.CancelledError:
            # The job was cancelled (quit, New village) mid-create: `docker run` keeps going,
            # so wait for it and remove the container rather than leak it.
            await asyncio.shield(cls._discard(run, name))
            raise
        return cls(name)

    @staticmethod
    async def _discard(run: asyncio.Future[str], name: str) -> None:
        try:
            await run
        except SandboxError:
            return  # never created
        try:
            await _run("docker", "rm", "-f", name)
        except SandboxError:
            pass  # best effort: a failure here must not replace the cancellation that got us here

    async def exec(
        self,
        argv: list[str],
        *,
        env_names: tuple[str, ...] = (),
        on_line: Callable[[str], None] | None = None,
        check: bool = True,
        workdir: str | None = WORKDIR,
    ) -> tuple[int, str]:
        cmd = ["docker", "exec"]
        # `-e NAME` without a value forwards it from our environment, so
        # secrets never appear on a command line.
        for n in env_names:
            cmd += ["-e", n]
        if workdir:
            cmd += ["-w", workdir]
        cmd += [self.name, *argv]
        proc = await _spawn(
            *cmd,
            # No stdin: `claude -p` otherwise waits for piped input.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=STREAM_LIMIT,
        )
        assert proc.stdout is not None
        lines: list[str] = []
        try:
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip("\n")
                lines.append(line)
                if on_line:
                    on_line(line)
            code = await proc.wait()
        finally:
            if proc.returncode is None:  # cancelled or timed out: don't leave the docker client behind
                proc.kill()
        out = "\n".join(lines)
        if check and code != 0:
            raise SandboxError(f"`{' '.join(argv[:3])}` failed ({code}): {_tail(out)}")
        return code, out

    async def copy_in(self, host_path: Path, dest: str) -> None:
        await _run("docker", "cp", str(host_path), f"{self.name}:{dest}")

    async def copy_out(self, src: str, host_path: Path) -> None:
        host_path.parent.mkdir(parents=True, exist_ok=True)
        await _run("docker", "cp", f"{self.name}:{src}", str(host_path))

    async def kill_processes(self) -> None:
        # `kill -9 -1` signals every process the user can, except PID 1 (the box's `sleep`) and itself.
        await self.exec(["sh", "-c", "kill -9 -1"], check=False, workdir=None)

    async def destroy(self) -> None:
        try:
            await _run("docker", "rm", "-f", self.name)
        except SandboxError:
            pass


async def image_exists(image: str = IMAGE) -> bool:
    try:
        proc = await _spawn(
            "docker", "image", "inspect", image,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
    except SandboxError:
        return False
    return await proc.wait() == 0


async def build_image(image: str = IMAGE, on_line: Callable[[str], None] | None = None) -> None:
    proc = await _spawn(
        "docker", "build", "-t", image, str(DOCKERFILE_DIR),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, limit=STREAM_LIMIT,
    )
    assert proc.stdout is not None
    tail: list[str] = []
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        tail = (tail + [line])[-10:]
        if on_line:
            on_line(line)
    if await proc.wait() != 0:
        raise SandboxError("docker build failed:\n" + "\n".join(tail))


_build_lock: asyncio.Lock | None = None


async def ensure_image(image: str = IMAGE) -> None:
    """Build the black-box image on first use (packaged installs have no `orc build-image` step to run).

    Only the default image is built: its Dockerfile ships with orc. The first job waits for the build.
    """
    global _build_lock
    if image != IMAGE or await image_exists(image):
        return
    _build_lock = _build_lock or asyncio.Lock()
    async with _build_lock:
        if not await image_exists(image):
            await build_image(image)
