# orc

A Python program that connects to an AI provider's API and lets you create agents with different personas, then orchestrate them.

Status: early prototype. The desktop app (`web/`) runs on a simulated engine; the Python side has the model-provider layer.

## Desktop app

An RTS-style board where agents are orc peons: train them, give orders, approve plans, watch them work.

```bash
cd web
npm install
npm start          # build + open the app window
```

For development with hot reload, run `npm run dev` in one terminal and `npm run dev:app` in another.

Controls: click or drag-box to select, shift-click to add, right-drag or WASD/arrows to pan, scroll to zoom,
middle-drag or `Q`/`E` to rotate, `H` to recentre, `Enter` to give orders, `Esc` to deselect,
`F11` fullscreen, `F12` devtools.

Note: with npm 11+ and Node 26, Electron's binary download can extract incompletely
(`Electron failed to install correctly`). Fix by unzipping the cached download by hand:

```bash
cd web/node_modules/electron && rm -rf dist && mkdir dist \
  && unzip -q "$(find ~/.cache/electron -name 'electron-v*-linux-x64.zip' | head -1)" -d dist \
  && printf electron > path.txt
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Model backends

orc can run models through either:

- **CLI subscriptions** (no API key): `claude`, `cursor-agent`, `copilot`, `codex`. orc runs them in non-interactive mode and they use whatever login you already have.
- **Anthropic API** (key billing): `AnthropicProvider` in `src/orc/providers.py`.

```python
from orc.providers import cli_provider
cli_provider("claude").complete("Summarize this repo")
```

## Anthropic credentials

The SDK reads credentials from the environment; nothing is stored in the repo.
Either export a key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

or log in with the Anthropic CLI (`ant auth login`).

## Run

```bash
orc            # or: python -m orc
```

## Tests

```bash
pytest
```

## Layout

```
src/orc/      Python package (model providers)
tests/        Python tests
web/          desktop app: Electron shell, Three.js world, React HUD, simulated engine
.venv/        local virtualenv (git-ignored)
```
