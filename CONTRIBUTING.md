# Contributing

Thanks for helping build orc.

1. Read [AGENTS.md](AGENTS.md). It has the setup commands, the invariants that keep the black boxes
   safe, and the places that must change together when the event protocol changes.
2. Make focused changes on a branch. Keep commits small with a one-line summary and a short body
   explaining why.
3. Before opening a pull request, run:

   ```bash
   .venv/bin/pytest
   cd web && npm run typecheck && npm run build
   ```

   For anything visual, run the app (`ORC_SIM=1 npm start` is enough for most UI work) and include a
   screenshot in the PR.
4. New runtimes need a replay test from a real captured transcript, with account data stripped.

Bugs and ideas: open an issue with what you did, what you expected, and what happened (logs from the
peon's panel help a lot). Security problems: see [SECURITY.md](SECURITY.md).
