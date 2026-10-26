# Security

orc runs AI agents with full permissions. This file explains what keeps that safe, and where the limits
are, so you can decide what to point it at.

## What the black boxes protect

- **Your files.** Agents run in Docker containers with no host directories mounted. They work on a
  clone made from a git bundle of your repo's `HEAD`. The only write orc makes to your repo is fetching
  a finished branch as `refs/heads/orc/...`; it never checks out, resets or edits your working tree.
- **Your machine's configuration.** Your home directory, `~/.ssh`, `~/.claude`, `~/.cursor`, shell
  history and other projects are not visible inside a box.
- **Privilege.** Boxes run as the unprivileged `node` user with `--cap-drop ALL`,
  `--security-opt no-new-privileges`, and memory, CPU and process limits.
- **Command lines.** Credentials are forwarded by variable name (`docker exec -e NAME`), so they don't
  appear in process listings or orc's logs.

## What they don't protect

- **The credential you give the boxes.** An agent inside a box can read the token or API key orc
  forwards to it, because the agent needs it to work. Treat that token as usable by the agent: prefer
  tokens you can revoke on their own (`claude setup-token`, a dedicated Cursor API key), and revoke them
  if something looks wrong.
- **Network.** Boxes have outbound internet access (agents need their model APIs and package
  registries). A misbehaving agent could send repository contents or the forwarded credential elsewhere.
  Don't point orc at code you aren't allowed to share with the model provider anyway.
- **What you merge.** Branches are written by agents. Review them before merging, and be careful with
  changes to CI configuration, build scripts and dependencies.
- **Docker itself.** Containers are not a hard security boundary against kernel or Docker escapes. If
  your Docker daemon is rootful (the default), a container escape would be root on the host. Rootless
  Docker or Podman reduces that risk.
- **Other local programs.** `orc serve` listens on `127.0.0.1` only. When the desktop app starts it,
  the app generates a random secret per launch and the engine accepts only connections that present
  it, and it rejects WebSocket connections from web pages (browsers allow any site to open a socket to
  localhost; the Origin check stops that). A program running as your user could still read the secret
  from the process environment; that user already has access to everything orc does. If you run
  `orc serve` by hand, it has no secret, so only the Origin check applies. Don't bind it to other
  interfaces.

## Saved credentials

Tokens pasted into **Settings** are written to `~/.config/orc/env`, created with mode 600 and replaced
atomically. Only known names can be written (Claude token, Anthropic key, Cursor key). Values are
never sent back to the app, logged, or included in events. To remove one, use **Remove** in Settings or
delete its line from the file, and revoke it at the provider if it may have leaked.

## Reporting a problem

Please open a private security advisory on GitHub (Security → Report a vulnerability) rather than a
public issue, with steps to reproduce.
