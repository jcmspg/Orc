# Architecture

New here? [GETTING_STARTED.md](GETTING_STARTED.md) covers setup; [TROUBLESHOOTING.md](TROUBLESHOOTING.md) covers
things that break (engine offline, missing credentials or image).

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
| `board.reset` | | Clear everything: units, edges, gold, war target. Starts a snapshot, or a new village mid-session. |
| `board.repo` | `repo` | The war target (absolute path, URL or `user@host:path` over ssh), or `null`. |
| `board.remote` | `remote` | `{target, port, key}` when the war target is an ssh repo (`key` is a file name in `~/.ssh`, never a path or contents), else `null`. Cleared by `board.reset` and by a local `setRepo`. |
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
| `branch.delivered` | `delivery` (full `Delivery`) | A branch landed (in a snapshot, or just delivered): id, branch, task, owner, repo, base, `baseBranch`, head, stat, status, note, command, conflicts, `mergeCommit`, bundle, at. |
| `branch.status` | `id, status, note, command, conflicts` | A delivery changed: `delivered`, `bundled`, `merged`, `conflict`, `missing`, `moved`. `command` is a git command for the player to run. |
| `branch.merged` | `id, into, commit` | orc merged the delivery into `into`. |
| `branch.diff` | `id, text, truncated, error` | Reply to a `diff` op: `git diff base...branch` (cut at 400 kB). Broadcast, not saved. |
| `app.credentials` | `status {NAME: env\|saved\|missing}, error?` | Which agent credentials exist. Never values. |

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
| `setRemote` | `target` (`user@host:path`), `port?`, `key?` (a file name in `~/.ssh`). Validated by the engine; replies with `board.repo` + `board.remote`, or an error log on the chieftain. |
| `setCredential` | `name`, `value` (handled by the server, saved via `credentials.py`) |
| `clearCredential` | `name` |
| `diff` | `id` (a delivery; the reply is a `branch.diff` event) |
| `merge` | `id` (a delivery; the outcome is `branch.merged` or `branch.status`) |
| `newVillage` | (none) Start over: see below |

Unknown or invalid ops are ignored. The app does not queue ops while offline.

**newVillage** cancels every worker and pending handoff (`Engine._later` timers), cancels open gates
and decisions, destroys every live black box (`Work.box`; temporary plan/review boxes are destroyed as
their jobs unwind), clears works and resets the id and hut counters. It then emits `board.reset`
followed by the default village (chieftain + 2 coders + reviewer, no war target, 0 gold), logs on the
chieftain how many jobs were stopped, and saves immediately. Ops that arrive while this runs are
ignored. Branches already fetched into the target repo are not touched. Clients handle the mid-session
`board.reset` like a snapshot's: the reducer empties the board and the World removes every peon, hut,
prop and label. The simulation (`FakeEngine.newVillage`) does the same with its own timers.

## Connection security and lifecycle

- The desktop shell generates a random `ORC_SESSION_TOKEN` per launch, passes it to the engine in its
  environment and to the page as `?token=` in the WebSocket URL. The server rejects a missing or wrong
  token (when one is configured) and any browser Origin that isn't local (`origin_allowed`).
- The page talks to the shell only through `web/electron/preload.cjs`: `quit()`, `openExternal(url)`
  (https, allow-listed hosts), `onQuitRequest(cb)`.
- Quit: closing the window or `Ctrl+Q` asks the page, which shows a confirmation; confirming calls
  `quit()`. The shell then sends the engine SIGTERM and waits (up to 10 s) for it to exit; the engine's
  lifespan shutdown cancels workers and destroys boxes. SIGTERM/SIGINT to the shell take the same path.
  The engine also polls `ORC_PARENT_PID` and stops itself if the shell vanished without stopping it.

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
chieftain, animate gold, destroy the coder's box. It also records a `Delivery` on the board and emits
`branch.delivered`. The `Work` remembers the base commit and the branch HEAD was on when the box cloned
(`gitops.bundle_head` returns it); a detached HEAD leaves `baseBranch` null.

## Spoils: diff and merge

Delivered branches are the board's `deliveries`, shown in the app's Spoils panel (click the gold, or `G`).

- **diff**: `gitops.diff` runs `git diff base...branch` against the repo's object store (never the working
  tree), read up to 400 kB. Bundled deliveries are diffed in a scratch bare repo. Each request first
  re-checks the delivery against the repo.
- **merge**: only into `baseBranch`, and only plumbing: `git merge-tree --write-tree` (merged tree, or the
  conflicting files), `git commit-tree` with both parents, `git update-ref <new> <old>` so a concurrent move
  of the target fails instead of being overwritten. Nothing moves if any worktree (`git worktree list
  --porcelain`) has the target checked out, which is the usual case for `main`: the delivery gets the
  command to copy (`git merge --no-ff <branch>`) and a note saying why. Conflicts move nothing either; the
  files are listed and the status is `conflict`. A detached base, a `moved` branch (tip no longer the
  delivered commit) and remote repos (`bundled`: no repo to merge in) also get a command, never a merge.
- **outside changes**: a branch deleted by the player becomes `missing`, one moved becomes `moved`, one
  whose delivered commit reached the target becomes `merged`. Checked on boot, on `diff` and on `merge`,
  and logged on the chieftain.

## Remote war targets (ssh)

`setRemote` points the war target at a repo on another machine. `gitops` stays the only module that
touches it, with the same rules as a local repo; git simply runs there through `ssh`.

- **In and out**: code reaches a box only as a bundle (`git bundle create - HEAD` on the remote, streamed back
  over ssh into the bundle cache, header-checked), never a mounted path. A finished branch goes out as a bundle
  streamed into a private (`mktemp`, mode 600) scratch file on the remote, `git fetch`ed into
  `refs/heads/orc/...` without `+`, and the scratch file is removed.
- **Merge**: the same plumbing (`merge-tree`, `commit-tree`, `update-ref <new> <old>`) and the same `git worktree
  list --porcelain` check, all executed on the remote. If any step cannot be answered (ssh down, old git) nothing
  moves. A checked-out target, a conflict or an unreachable remote hand the player a command to paste:
  `ssh -p N -i ~/.ssh/KEY -t user@host 'cd path && git merge --no-ff orc/...'`.
- **Validation** (`gitops.parse_remote`, before anything runs): `user@host:path` with host/path restricted to
  `[A-Za-z0-9._/+@%=,-]`, no leading `-` or `~`, no whitespace, quotes or shell syntax, no `ssh://`; port 1-65535;
  key a bare file name (`[A-Za-z0-9][A-Za-z0-9._-]*`, so no `/` and no `..`) that must exist as a file in `~/.ssh`.
  Only `orc/...` branch names are fetched or merged, and revisions can't start with `-`.
- **ssh**: argv list only; `--` precedes the host; one command string whose every word is `shlex`-quoted for the
  remote shell. Options: `BatchMode=yes` (never prompts), `ConnectTimeout=10`, `StrictHostKeyChecking=yes`
  (unknown hosts are refused: connect once from a terminal first), `-a`/`ForwardAgent=no`, `-x`,
  `ClearAllForwardings=yes`, `PermitLocalCommand=no`, no password auth, `-T`; with a key, `IdentitiesOnly=yes -i
  ~/.ssh/<name>`. Hard time limits kill a hung ssh (60 s per command, 30 min per bundle transfer). orc passes
  the key's path to ssh and never reads the key; nothing key-like is logged, evented or saved except its name.
- **Down is not gone**: an unreachable remote changes no delivery's status (`_look` returns no change); a
  delivery into an old remote that is no longer the war target is left alone and diff/merge say so.
- **Persistence**: `remote` is saved beside `repo`. Boot registers it without contacting the host; if the key
  file has vanished the war target is dropped.

Timing constants (`WALK_S`, `FLIGHT_S`) make the engine wait while the app animates walks and flights,
so what you see stays in step with what happens.

## Persistence

`persist.BoardStore` saves the board (orcs with their logs, edges, gold, war target) to
`$XDG_STATE_HOME/orc/board.json`. Deliveries are saved too and re-checked against the repo on boot. The engine schedules a save `SAVE_DELAY_S` after any state-bearing
event (visual-only events are skipped) and saves once more on shutdown; writes are atomic and mode 600.

On boot, a saved village is replayed through `_emit` as `board.repo`, `orc.spawned`, `edge.added` and
`gold.banked`, so the board and any client stay in step. Jobs, gates, retries and boxes are not saved
(boxes are destroyed on shutdown), so every orc is restored `idle` with no task or pending decision,
and one that was busy logs that its work was lost. A war target that no longer validates is dropped.
An unreadable file is moved to `board.json.bad` and reported on the chieftain; a fresh village starts.
The `newVillage` op overwrites the file with the new default village right away.

## Runtimes

`Runtime.run(box, prompt, on_log, *, kind, hints) -> str`. `kind` is `plan`, `code` or `review`;
`hints` carries structured context that only the dummy runtime uses.

- `ClaudeCodeRuntime`: `claude -p ... --output-format stream-json --verbose --dangerously-skip-permissions`;
  `assistant` text → `info` lines, `tool_use` → `cmd` lines, `result` → the return value.
- `CursorRuntime`: `cursor-agent -p ... --output-format stream-json --force --trust --sandbox disabled`;
  `tool_call` / `started` → `cmd` lines; errors arrive as plain text on stderr.
- `CodexRuntime`: `codex exec --json --dangerously-bypass-approvals-and-sandbox -- <prompt>` with
  `CODEX_API_KEY`; `item.started` `command_execution` and `item.completed` `file_change` → `cmd` lines,
  `agent_message` → `info` lines (the last one is the return value), `turn.completed` / `turn.failed`
  end the run (`error` events before them are transient reconnects).
- `CopilotRuntime`: `copilot -p ... --output-format json --allow-all --no-ask-user --disable-builtin-mcps
  --no-auto-update` with `COPILOT_GITHUB_TOKEN`; `tool.execution_start` → `cmd` lines,
  `assistant.message` content → `info` lines (the last one is the return value), `result.exitCode`
  decides success; auth errors arrive as plain text. The built-in GitHub MCP server stays off so a box
  can't act on the user's GitHub account.
- Error details from every runtime have credential values redacted, since APIs may echo a rejected key.
- `DummyRuntime` (`ORC_FAKE_AGENT=1`): no model calls.

## The app

- `web/src/store.ts`: Zustand store; `dispatch(event)` reduces and fans out to listeners.
- `web/src/world/World.ts`: builds huts, props and peons from `orc.spawned`, routes visual events to
  `Peon` and `Effects`, owns the RTS camera (auto-fits the village into the space the HUD leaves free).
- `web/src/world/Peon.ts`: per-state procedural animation; walks for crates/gold; CSS2D labels.
- `web/src/engine.ts`: the in-app simulation implements the same `Engine` interface with timers; it fakes diffs and both merge outcomes (a checked-out `main`, a free `develop`).
- `web/src/Spoils.tsx`: the Spoils panel, diff viewer, merge confirmation and copyable command.
