# Getting started

This guide takes you from a fresh clone to a running orc village.

## Prerequisites

- Linux or macOS
- Python 3.11+
- Node 20+ (with npm)
- Docker, running, and usable by your user (`docker ps` should work)
- git

## Setup

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cd web && npm install
```

## Build the box image

Peons work inside Docker "black boxes". Build the image once (it takes a few minutes):

```bash
.venv/bin/orc build-image
```

You can skip this step if you only use simulation mode (`ORC_SIM=1`).

## Run

```bash
cd web && npm start
```

This opens the desktop app and starts the engine (`orc serve`) for you on port 47820.
Real agents need credentials; see the credentials table in the README.

### Try it without model calls

- `ORC_FAKE_AGENT=1 npm start` runs the real engine and real Docker boxes, but with a dummy agent
  that makes a trivial commit. No model calls, no credentials needed. Requires the box image.
- `ORC_SIM=1 npm start` runs a self-contained simulation inside the app. No engine, no Docker,
  no image build. Good for looking around the UI.

Both are run from the `web/` directory.

## Check your work

```bash
.venv/bin/pytest
cd web && npm run typecheck
cd web && npm run build
```

See the README for configuration options and troubleshooting, and `ARCHITECTURE.md` for how the
engine and app fit together.
