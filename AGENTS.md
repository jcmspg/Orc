# AGENTS.md

Guidance for AI coding agents (and humans) working on this repository. Read this before changing code.

## What this is

orc is a desktop app for orchestrating AI coding agents, presented as an RTS. Two halves:

- **Engine** (`src/orc/`, Python 3.11+): a FastAPI WebSocket server (`orc serve`) that owns the board,
  runs jobs in Docker "black boxes", and narrates everything as events.
- **App** (`web/`, TypeScript): Electron shell, a Three.js 3D village (`src/world/`), and a React HUD.
  It renders events and sends player ops. It can also run a self-contained simulation (`?sim`).

The two halves share one contract: the event and op shapes. See `docs/ARCHITECTURE.md`.

## Commands

```bash
# setup
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd web && npm install

# check your work (run all of these before you finish)
.venv/bin/pytest
cd web && npm run typecheck
cd web && npm run build

# run
.venv/bin/orc build-image              # needed once for real black boxes
cd web && npm start                    # app + engine
ORC_FAKE_AGENT=1 npm start             # real boxes, dummy agent: no model calls
ORC_SIM=1 npm start                    # simulation only: no engine, no Docker
```

Prefer project binaries by absolute or explicit path (`.venv/bin/...`, `web/node_modules/.bin/...`).
A bare `npx <tool>` from the wrong directory silently downloads an unrelated version.

## Invariants (don't break these)

1. **Never mount host paths into a box.** Code enters only as a git bundle (`docker cp`) and leaves
   only as a git bundle fetched into the target repo as `refs/heads/orc/...`. `gitops.py` is the only
   module that touches the user's repo, and it must never check out, reset, or write the working tree.
2. **Credentials travel by name.** Pass secrets to boxes with `env_names` (→ `docker exec -e NAME`),
   never as argv values or file contents. Never log, print, or commit token values.
3. **The event contract is shared.** Any change to an event or op must be made in all of:
   `src/orc/board.py` (apply + snapshot), `src/orc/engine.py` (emitters/handlers),
   `web/src/types.ts` (`OrcEvent`, `Engine`), `web/src/store.ts` (reducer),
   `web/src/remote.ts`, `web/src/engine.ts` (simulation), and the World if it animates it.
   Keep field names identical (camelCase on the wire, e.g. `durationMs`).
4. **The engine only changes state through `_emit`.** It applies the event to the board and broadcasts
   it, which keeps snapshots and live clients consistent.
5. **Visuals never lie.** Animations must be driven by real events. Don't add decorative state the
   engine doesn't know about.
6. **Agents in boxes may run with full permissions** (`--dangerously-skip-permissions`, `--force`)
   precisely because of 1 and 2. Don't add those flags anywhere outside a box.

## Code map

| Area | Files |
|---|---|
| Job flows, approvals, retries | `src/orc/engine.py` |
| Containers | `src/orc/sandbox.py`, `src/orc/docker/Dockerfile` |
| Agent CLIs inside boxes | `src/orc/runtimes.py` |
| Prompts and reply parsing | `src/orc/prompts.py` |
| Host repo operations | `src/orc/gitops.py` |
| Server | `src/orc/server.py`, `src/orc/__main__.py` |
| 3D world | `web/src/world/` (`World.ts` scene/camera/input, `Peon.ts` animation, `models.ts`, `terrain.ts`, `effects.ts`) |
| HUD | `web/src/App.tsx`, `web/src/styles.css` |
| Engine clients | `web/src/remote.ts` (real), `web/src/engine.ts` (simulation), `web/src/engines.ts` (picks one) |
| Desktop shell | `web/electron/main.cjs` |

## Adding a runtime

1. Install its CLI in `src/orc/docker/Dockerfile` (as the `node` user if it installs per-user).
2. Add a class in `runtimes.py` implementing `run(box, prompt, on_log, *, kind, hints) -> str`:
   run the CLI non-interactively with streaming JSON if it has it, turn events into
   `on_log(text, "info" | "cmd")` lines, return the final answer text, raise `RuntimeError` with a useful
   message on failure. Check for its credential first and explain where to get it.
3. Register it in `runtime_for` and mark it ready in `BOX_READY` in `web/src/App.tsx`.
4. Test it by replaying a real captured transcript (see `tests/test_runtimes.py` and
   `tests/data/claude_stream.jsonl`). Strip account data (usage, rate limits, paths) from fixtures.
5. Document its credential in the README table.

## Testing

- `tests/fakes.py` has `LocalSandbox`: a temp directory standing in for a container, with
  `/home/node/work` and `/tmp` mapped into it. Commands, including git, run for real.
- `DummyRuntime` makes a trivial commit, plans one task per worker, and rejects each first review. Use
  it with `walk_s` / `flight_s` near zero for fast flow tests.
- Engine tests drive the engine through `engine.handle({...op...})`, the same entry point the server uses.
- UI changes: run the app (`ORC_SIM=1` is enough for most) and look at it. In dev builds
  `window.__orc` exposes `{ engine, board, select }` for scripted checks.

## Style

- Python: type hints, dataclasses, `async` for anything that waits on processes, small focused
  functions, docstrings where the *why* isn't obvious. Match the surrounding comment density.
- TypeScript: strict mode, no `any`, functional React components, Zustand selectors that return stable
  references (don't build new arrays/objects inside a selector).
- User-facing text stays in the orc theme, but log lines must still say plainly what happened.
- Keep commits focused, with a one-line summary and a short body explaining why.

## Gotchas

- Vite HMR can serve a stale module after rapid multi-file edits (symptom: a crash referencing a field
  that clearly exists). Restart the dev server with `--force` and reload.
- `CSS2DRenderer` owns the `display` style of label holders; hide labels with `object.visible`, and
  animate the inner element, not the holder.
- `.game` uses `isolation: isolate` so label z-indexes can't rise above HUD panels.
- `claude -p` waits for stdin if it is open; box execs use `stdin=DEVNULL`.
- Port 8787 is commonly taken by other local tools; orc uses 47820.
