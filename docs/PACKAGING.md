# Packaging

orc ships as an Electron app with the Python engine frozen next to it.

```
installer
  orc (Electron)  --spawns-->  resources/engine/orc-engine[.exe]   (PyInstaller onedir)
                                 _internal/orc/docker/Dockerfile   (for build-image + first-use image build)
```

The host still needs **Docker** and **git**; they are not bundled. The engine builds the `orc-peon` image
on first use if it is missing (`sandbox.ensure_image`), and `orc-engine build-image` does it by hand.

## Build locally

PyInstaller and Electron's packager cannot cross-compile: build each OS on that OS.

```bash
# 1. Engine (any OS). Windows: use .venv\Scripts\python and .venv\Scripts\pip.
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]" pyinstaller
.venv/bin/python scripts/build-engine.py                          # -> dist/engine/orc-engine/
.venv/bin/python scripts/smoke-engine.py dist/engine/orc-engine/orc-engine   # optional; .exe on Windows

# 2. App
cd web && npm ci
npm run pack                      # unpacked app in web/release/<os>-unpacked, quick to test
npm run dist                      # installers in web/release/
npm run dist -- --mac --arm64     # or --mac --x64 (the engine must match: build it on that arch)
npm run dist -- --win --x64
```

| OS | Output | Notes |
|---|---|---|
| Linux | `.AppImage`, `.deb` | `.deb` needs `fpm` (downloaded by electron-builder) |
| macOS | `.dmg` per arch | build on an Apple Silicon runner for arm64, an Intel runner for x64 |
| Windows | NSIS `.exe` | per-user or per-machine, directory choice |

The electron-builder config lives in the `build` field of `web/package.json`; the engine spec in
`packaging/orc-engine.spec`.

## How the app finds and stops the engine

- Packaged: `process.resourcesPath/engine/orc-engine[.exe]`. Dev: `.venv/bin/orc` (`.venv\Scripts\orc.exe` on Windows).
- The app passes `ORC_SESSION_TOKEN`, `ORC_PARENT_PID` and `ORC_STOP_ON_STDIN_EOF=1` and keeps the engine's stdin open.
- Clean stop: on quit the app sends SIGTERM (Linux/macOS) or closes stdin (Windows has no gentle signal). Either runs
  uvicorn's shutdown, which destroys the black boxes. After 10 s the app kills it.
- Crash safety: the engine also exits if its parent PID disappears (`server.pid_alive`, which uses `OpenProcess` on Windows
  because `os.kill(pid, 0)` would terminate the process there) or if stdin reaches EOF.

## CI

`.github/workflows/release.yml` runs on tags `v*` and by hand (`workflow_dispatch`). Each matrix entry (linux-x64,
mac-arm64, mac-x64, win-x64) freezes the engine, smoke-tests it, builds the installers and uploads them as workflow
artifacts. On a tag it also creates a **draft** GitHub Release with every installer. To release:
`git tag v0.1.0 && git push origin v0.1.0`, then review and publish the draft.

## Follow-up: signing and notarization (not done)

Builds are unsigned. Users see warnings until this is done.

- **macOS**: needs an Apple Developer ID certificate and notarization. Add secrets `CSC_LINK` (base64 .p12),
  `CSC_KEY_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`; set `mac.hardenedRuntime`,
  `mac.entitlements` and `mac.notarize` in the build config. The frozen engine under `Resources/engine` must be signed
  too (electron-builder signs nested binaries when `mac.binaries` lists them), and with the hardened runtime it needs
  the `com.apple.security.cs.allow-unsigned-executable-memory` / `disable-library-validation` entitlements.
- **Windows**: an Authenticode certificate (or Azure Trusted Signing). Set `CSC_LINK` / `CSC_KEY_PASSWORD` or the
  `win.azureSignOptions` block. Unsigned installers trigger SmartScreen.
- **Linux**: nothing required. Optionally sign the `.deb` / publish an apt repo or Flatpak.
- **Auto-update** (`electron-updater`) only makes sense once builds are signed.
- **Icons**: the default Electron icon is used. Add `web/build/icon.png` (1024x1024) and electron-builder picks it up.
