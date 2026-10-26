# orc

An RTS-style desktop app for orchestrating AI coding agents. Agents are orc peons: you train them,
point the chieftain at a repo, give orders, approve plans, and watch them work. Every peon works in
its own disposable Docker container (a "black box"); finished, reviewed work lands in your repo as an
`orc/...` branch for you to merge.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/orc build-image        # builds the orc-peon Docker image (needs Docker)

cd web && npm install
```

Give Claude Code a credential for use inside the black boxes. Your `~/.claude` is never mounted, so
the boxes need a token of their own:

```bash
claude setup-token                       # prints a long-lived token for your subscription
export CLAUDE_CODE_OAUTH_TOKEN=...       # or ANTHROPIC_API_KEY=... for API billing
```

Keep the export in a private file outside the repo (for example `~/.config/orc/env`, mode 600)
sourced from your shell rc. orc passes the variable into boxes by name, never on a command line.

## Run

```bash
cd web && npm start        # builds the app, starts `orc serve`, opens the window
```

1. Select **Ozgar** (the chieftain), set the **war target** to a local repo path, press Set.
2. With Ozgar selected, type an order ("Add input validation to the signup form") and press Enter.
3. Ozgar reads the repo in a black box and proposes a battle plan. His autonomy is off, so approve it.
4. Coders work on their own branches in their own boxes; the reviewer approves or sends findings back.
5. Gold at the town hall means a branch landed: `git branch --list 'orc/*'` in your repo.

Variants:

- `ORC_FAKE_AGENT=1 npm start`: real boxes and git, but a dummy agent instead of a model (free; for testing).
- `ORC_SIM=1 npm start`: the old in-app simulation, no server or Docker at all.
- `ORC_PORT=...`: engine port (default 47820). `ORC_NO_SERVER=1` if you run `orc serve` yourself.
- Development with hot reload: `npm run dev` in one terminal, `npm run dev:app` in another.

Controls: click or drag-box to select, shift-click to add, right-drag or WASD/arrows to pan, scroll to
zoom, middle-drag or `Q`/`E` to rotate, `H` to recentre, `Enter` to give orders, `Esc` to deselect,
`F11` fullscreen, `F12` devtools.

## How the black boxes work

- One container per job, from the `orc-peon` image (Node, git, Claude Code), running as a non-root
  user with all capabilities dropped, 4 GB memory, 2 CPUs. No host directories are mounted.
- The repo goes in as a `git bundle` of your current HEAD (uncommitted changes are not included).
- Inside, Claude Code runs with `--dangerously-skip-permissions`: it can do anything in the box, and
  the box is thrown away afterwards.
- Work comes out as a bundle and is fetched into your repo as a branch. Nothing else on your machine
  is written, and your checked-out branch and working tree are never touched.
- Remote repo URLs work for cloning; their finished branches are left as bundles in `~/.cache/orc/bundles`.

Runtimes: only `claude` runs inside the boxes so far. Peons trained with cursor, copilot or codex
fail with a clear message until those CLIs are added to the image.

## Model providers (Python library)

`src/orc/providers.py` can also call models directly: the Anthropic API (`AnthropicProvider`, needs
`ANTHROPIC_API_KEY`) or the host CLIs (`cli_provider("claude")` and friends) using your existing logins.

## Tests

```bash
.venv/bin/pytest
```

Engine tests run full flows with real git in temp-dir "boxes" and a dummy agent, so they need neither
Docker nor model calls.

## Layout

```
src/orc/engine.py     job queues, plan/code/review flows, approvals, retries
src/orc/sandbox.py    Docker black boxes        src/orc/docker/Dockerfile  the orc-peon image
src/orc/runtimes.py   agent CLIs inside boxes   src/orc/prompts.py         role prompts + parsers
src/orc/server.py     WebSocket server          src/orc/board.py           server-side board state
web/                  desktop app: Electron shell, Three.js world, React HUD
tests/                Python tests
```

## Troubleshooting

With npm 11+ and Node 26, Electron's binary download can extract incompletely
(`Electron failed to install correctly`). Fix by unzipping the cached download by hand:

```bash
cd web/node_modules/electron && rm -rf dist && mkdir dist \
  && unzip -q "$(find ~/.cache/electron -name 'electron-v*-linux-x64.zip' | head -1)" -d dist \
  && printf electron > path.txt
```
