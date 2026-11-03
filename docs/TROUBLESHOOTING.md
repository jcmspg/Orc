# Troubleshooting

## Credentials

Credentials are passed to boxes by name only. This page lists names, never values. Don't paste token
values into issues, logs or commits.

| Runtime | Credential name(s) |
|---|---|
| `claude` | `CLAUDE_CODE_OAUTH_TOKEN` (subscription) or `ANTHROPIC_API_KEY` (API billing) |
| `cursor` | `CURSOR_API_KEY` |
| `codex` | `CODEX_API_KEY` (an OpenAI API key; a ChatGPT sign-in can't be used) |
| `copilot` | `COPILOT_GITHUB_TOKEN` (fine-grained token with *Copilot Requests*; `GH_TOKEN` / `GITHUB_TOKEN` are not forwarded) |

These are exactly the names in `credentials.KNOWN`. See the README's
[Runtimes and credentials](../README.md#runtimes-and-credentials) for where to get each one.

- Set them in **Menu → Settings**, or in `~/.config/orc/env` as `export NAME=value` lines (mode 600).
- A variable already set in your environment takes priority over the file.
- The app only shows *Saved*, *From environment* or *Not set*, never the value.
- A peon fails with a "missing credential" message: the runtime's name above is *Not set*. Set it and retry.

## Port 47820

The engine listens on 47820 (8787 is avoided because other local tools often use it). If the app can't
connect or the engine fails to start, something else may hold the port:

```bash
ss -ltnp | grep 47820      # Linux
lsof -i :47820             # macOS
```

Stop the other process (often a leftover `orc serve`) and start again.

## Docker

- **"Cannot connect to the Docker daemon"**: start Docker, and check `docker info` works as your user
  (on Linux, add yourself to the `docker` group and log in again).
- **Image missing**: run `.venv/bin/orc build-image` once before using real black boxes.
- **No Docker at all**: use `ORC_SIM=1 npm start` (simulation) to look around, or
  `ORC_FAKE_AGENT=1 npm start` with Docker but no model calls.
- **Stuck or leftover boxes**: list them with `docker ps -a` and remove any you recognise as orc's with
  `docker rm -f <id>`.

## Testing without touching your village

The app restores and saves your real village and credentials. When running it for tests, point the XDG
directories at a scratch directory so `~/.config/orc/env` and `~/.local/state/orc/board.json` are untouched:

```bash
scratch=$(mktemp -d)
XDG_CONFIG_HOME=$scratch/config XDG_STATE_HOME=$scratch/state ORC_SIM=1 npm start
```

The pytest suite already isolates both via `tests/conftest.py`.
