# Architecture

```
┌──────────── desktop app (web/) ────────────┐        ┌────────────── engine (src/orc/) ───────────────┐
│ Electron shell ── starts `orc serve`        │        │ server.py   FastAPI, one WebSocket: /ws         │
│ React HUD  ──ops──► RemoteEngine ──────────────ws──► │ engine.py   per-orc queues, flows, gates        │
│ Three.js World ◄── store ◄── events ◄──────────ws─── │ board.py    authoritative state + snapshot      │
└─────────────────────────────────────────────┘        │ runtimes.py claude / cursor / dummy             │
                                                       │ sandbox.py  docker run / exec / cp / rm         │
                                                       │ gitops.py   bundle HEAD, fetch orc/* branches   │
                                                       └───────────────┬────────────────────────────────┘
                                                                       │ docker
                                                    ┌──────────────────▼───────────────────┐
                                                    │ orc-peon boxes (one per job)          │
                                                    │ /home/node/work: the cloned repo      │
                                                    └───────────────────────────────────────┘
```

## Event protocol

Server → app. Every message is one JSON event. The app's reducer (`web/src/store.ts`) applies the
state-bearing ones; the World (`web/src/world/World.ts`) animates the visual ones.

| Event | Fields | Meaning |
|---|---|---|
| `board.reset` | | Start of a snapshot: clear everything. |
| `board.repo` | `repo` | The war target (absolute path or URL), or `null`. |
| `orc.spawned` | `orc` (full `Orc`) | A unit exists (in a snapshot, or newly trained). |
| `edge.added` | `edge {from, to, kind}` | `command` (chieftain → worker) or `review` (worker → reviewer). |
| `orc.state` | `id, state, task?` | `idle`, `thinking`, `working`, `walking`, `reviewing`, `awaiting`, `failed`. |
| `orc.log` | `id, line, level?` | `info`, `cmd`, `ok`, `warn`, `error`. |
| `orc.say` | `id, text` | Speech bubble only. |
| `orc.awaiting` | `id, pending {kind, summary, items}` | Waiting for Approve / Reject. |
| `orc.resolved` | `id` | The pending decision was made. |
| `orc.autonomy` | `id, autonomy` | Toggle changed. |
| `orders.sent` | `from, to, label, durationMs` | Scroll flies from the chieftain to a worker. |
| `crate.sent` | `from, to, label: branch\|findings, durationMs` | A peon walks a branch (or findings) over. |
| `gold.delivered` | `from, to, durationMs` | A peon carries gold to the town hall. |
| `gold.banked` | `amount` | Gold counter increments. |

On connect, the server sends a snapshot (`board.reset`, `board.repo`, every `orc.spawned` with current
state, log and pending, every `edge.added`, accumulated gold) and then live events. Snapshot and
subscription happen with no `await` in between, so nothing is missed.

## Ops

App → server, `{ "op": ..., ... }`:

| Op | Fields |
|---|---|
| `spawn` | `role` (`coder`, `reviewer`, `tester`), `backend` (`claude`, `cursor`, `copilot`, `codex`) |
| `command` | `ids`, `text` |
| `approve` / `reject` | `id` (resolves a pending gate) |
| `retry` / `abandon` | `id` (resolves a failed job) |
| `setAutonomy` | `id`, `on` |
| `setRepo` | `repo` |

Unknown or invalid ops are ignored. The app does not queue ops while offline.

## Jobs and flows

Each orc has an `asyncio.Queue` of jobs and one worker coroutine, so an orc does one thing at a time
and further orders queue up (logged as "Queued: ..."). Every flow runs inside `_run_with_retries`: an
exception sets the orc to `failed`, logs the error, and waits for Retry (run the flow again) or Abandon
(destroy the box, drop the work).

**plan** (chieftain): require a war target → clone into a temporary box → runtime with
`prompts.plan` → parse `{"tasks": [{"worker", "task"}]}` → gate (`pending.kind = "plan"`) → for each
task emit `orders.sent` and, after the flight time, enqueue a `code` job on that worker.

**code** (coder/tester): create a `Work` (branch `orc/<peon>/<task-slug>-<id>`) → gate
(`pending.kind = "commands"`) → raise a box, clone at the current `HEAD`, create the branch → runtime
with `prompts.code` (or `prompts.fix` with review findings) → commit anything uncommitted → fail if
there are no commits → `git bundle` the branch out → walk a crate to the least-busy reviewer and
enqueue a `review` job (or deliver directly if there is no reviewer). The box stays alive for fixes.

**review**: clone the coder's bundle into a temporary box → runtime with `prompts.review` → parse
`{"approve", "findings"}` → if rejected and fewer than 2 fix rounds have happened, walk the findings back
and enqueue a `code` job with them on the same `Work`; otherwise deliver.

**deliver**: `git fetch <bundle> refs/heads/<branch>:refs/heads/<branch>` into the target repo (local
repos only; URL targets keep the bundle in `~/.cache/orc/bundles`), log the branch on the owner and the
chieftain, animate gold, destroy the coder's box.

Timing constants (`WALK_S`, `FLIGHT_S`) make the engine wait while the app animates walks and flights,
so what you see stays in step with what happens.

## Runtimes

`Runtime.run(box, prompt, on_log, *, kind, hints) -> str`. `kind` is `plan`, `code` or `review`;
`hints` carries structured context that only the dummy runtime uses.

- `ClaudeCodeRuntime`: `claude -p ... --output-format stream-json --verbose --dangerously-skip-permissions`;
  `assistant` text → `info` lines, `tool_use` → `cmd` lines, `result` → the return value.
- `CursorRuntime`: `cursor-agent -p ... --output-format stream-json --force --trust --sandbox disabled`;
  `tool_call` / `started` → `cmd` lines; errors arrive as plain text on stderr.
- `DummyRuntime` (`ORC_FAKE_AGENT=1`): no model calls.

## The app

- `web/src/store.ts`: Zustand store; `dispatch(event)` reduces and fans out to listeners.
- `web/src/world/World.ts`: builds huts, props and peons from `orc.spawned`, routes visual events to
  `Peon` and `Effects`, owns the RTS camera (auto-fits the village into the space the HUD leaves free).
- `web/src/world/Peon.ts`: per-state procedural animation; walks for crates/gold; CSS2D labels.
- `web/src/engine.ts`: the in-app simulation implements the same `Engine` interface with timers.
