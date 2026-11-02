import asyncio

import pytest

from orc import sandbox


def test_box_cancelled_mid_create_is_removed(monkeypatch):
    calls: list[tuple[str, ...]] = []
    started = asyncio.Event()

    async def fake_run(*cmd: str) -> str:
        calls.append(cmd)
        if cmd[1] == "run":
            started.set()
            await asyncio.sleep(0.05)  # docker run still going when the job is cancelled
        return ""

    monkeypatch.setattr(sandbox, "_run", fake_run)

    async def scenario():
        task = asyncio.create_task(sandbox.DockerSandbox.create("coder"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    name = calls[0][calls[0].index("--name") + 1]
    assert calls[-1] == ("docker", "rm", "-f", name)
