#!/usr/bin/env python3
"""Smoke-test a frozen engine: it starts, /health answers, the WebSocket accepts a client
(and rejects a wrong token), and closing its stdin stops it cleanly.

    python scripts/smoke-engine.py dist/engine/orc-engine/orc-engine

Runs with scratch config/state/cache dirs, so the real village is never touched.
Needs the `websockets` package (installed with uvicorn[standard]).
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

PORT = 47999
TOKEN = "smoke-token"


async def ws_check() -> None:
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws?token={TOKEN}") as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), 10))
        print("first event:", first.get("type"))
    try:
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws?token=wrong") as ws:
            await asyncio.wait_for(ws.recv(), 5)
        sys.exit("FAIL: wrong token was accepted")
    except (websockets.ConnectionClosed, websockets.InvalidStatus):
        print("wrong token rejected")


def main() -> None:
    exe = sys.argv[1]
    with tempfile.TemporaryDirectory() as scratch:
        env = {
            **os.environ,
            "XDG_CONFIG_HOME": os.path.join(scratch, "config"),
            "XDG_STATE_HOME": os.path.join(scratch, "state"),
            "XDG_CACHE_HOME": os.path.join(scratch, "cache"),
            "ORC_SESSION_TOKEN": TOKEN,
            "ORC_PARENT_PID": str(os.getpid()),
            "ORC_STOP_ON_STDIN_EOF": "1",
        }
        proc = subprocess.Popen([exe, "serve", "--port", str(PORT)], env=env, stdin=subprocess.PIPE)
        try:
            deadline = time.time() + 60
            while True:
                try:
                    body = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).read()
                    print("health:", body.decode())
                    break
                except OSError:
                    if proc.poll() is not None or time.time() > deadline:
                        sys.exit("FAIL: engine did not come up")
                    time.sleep(0.5)
            asyncio.run(ws_check())
            assert proc.stdin
            proc.stdin.close()  # the app's "stop cleanly" request
            try:
                code = proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                sys.exit("FAIL: engine ignored stdin close")
            print("engine exited with", code)
        finally:
            if proc.poll() is None:
                proc.kill()
    print("OK")


if __name__ == "__main__":
    main()
