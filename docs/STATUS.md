# Status and next steps

Where the project stands, so work can resume cold. Update this when you finish something on the list.
Last updated: 2026-11-05 (v0.1.0 tagged).

## Shipped

- Engine, app, and in-app merge as described in [ARCHITECTURE.md](ARCHITECTURE.md).
- Safety/robustness: agent runs time out after 30 min (`AGENT_TIMEOUT_S`; the agent is killed in the box,
  then the player gets Retry/Abandon); a bad op is reported in the chieftain's log, never raised; bundles are
  removed when work is abandoned or its branch is fetched, swept on boot, and the HEAD bundle is shared by jobs
  starting from the same commit.
- SSH war target (`board.remote`, op `setRemote`): see "Remote war targets" in ARCHITECTURE.md. Hardened
  (strict host keys, argv-only, branches confined to `refs/heads/orc/`); the remote merge does its worktree check
  and compare-and-swap on the remote.
- Packaging ([PACKAGING.md](PACKAGING.md)): PyInstaller engine + electron-builder installers (Linux AppImage/deb,
  macOS dmg x64+arm64, Windows NSIS x64). `release.yml` builds all four on a `v*` tag and makes a **draft** release.
  First job of a packaged install builds the `orc-peon` image and says so in the chieftain's log.

## Verified vs not

- CI (`ci.yml`): Python + TypeScript green. `release.yml`: all four platform builds passed on a manual run,
  including the engine smoke test on each OS.
- **Never launched by a human:** the macOS dmg and Windows installer, the Linux AppImage/deb, and a real
  Docker job from a packaged app. The pytest suite has only run on Linux.
- Not run end to end: the Electron app against a real engine with an ssh target; the first-use image build from the
  frozen engine.

## Open items (need the owner)

1. App icon: add `web/build/icon.png` (the default Electron icon is used now; electron-builder also warns that
   `desktopName` is unset).
2. Code signing/notarization for macOS and Windows (steps in PACKAGING.md; needs certificates). Until then
   Gatekeeper/SmartScreen warn, and an unsigned arm64 Mac build may refuse to run.
3. Try an installer on a Mac and a Windows machine, then publish the `v0.1.0` draft release.
4. Confirm the `macos-15-intel` runner label is available to the account.

## Ideas, not started

- Windows/Linux arm64 builds (x64 only today).
- Run pytest on Windows/macOS in CI.
- `npm` majors left alone on purpose: three 0.177 -> 0.187, vite 6 -> 8, typescript 5 -> 7.
- Dockerfile installs agent CLIs unpinned (rebuilds can change behaviour); pin if reproducibility matters.
- Persisting the board off the event loop (`BoardStore.save` is a small synchronous write, batched to 1 s).

## Gotchas learned

- Tests must isolate `XDG_CONFIG_HOME`, `XDG_STATE_HOME` and `XDG_CACHE_HOME` (conftest does); before this, tests
  littered the real `~/.cache/orc/bundles`.
- Cancelling a `docker exec` does not stop the process inside the box; `Sandbox.kill_processes()` does
  (`kill -9 -1`, which spares PID 1). Use it after a timeout.
- Dependency install scripts are allow-listed in `web/package.json` `allowScripts`, pinned by version: bump the
  entry when you bump Electron or esbuild.
- Never take lockfile hunks from peon branches: older npm versions strip the `libc` entries.
- GitHub occasionally rejects a push with "fatal error in commit_refs"; retrying worked.
