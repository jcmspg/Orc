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
- **The engine port.** `orc serve` listens on `127.0.0.1` only, without authentication. Any local
  process can send it orders. Don't bind it to other interfaces.

## Reporting a problem

Please open a private security advisory on GitHub (Security → Report a vulnerability) rather than a
public issue, with steps to reproduce.
