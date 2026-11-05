// Engine that talks to the Python server (`orc serve`) over a WebSocket.
// Server events are applied to the board as-is; HUD actions become ops.

import { board, dispatch } from "./store";
import type { Backend, CredentialName, Engine, OrcEvent, Role } from "./types";

export class RemoteEngine implements Engine {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private reconnect: number | null = null;
  private stopped = false;

  constructor(private readonly url: string) {}

  start(): void {
    if (this.ws) return;
    this.stopped = false;
    board.setState({ link: "connecting" });
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      board.setState({ link: "live" });
    };
    ws.onmessage = (m) => {
      try {
        dispatch(JSON.parse(m.data as string) as OrcEvent);
      } catch (err) {
        console.error("orc: dropped an unreadable event", err);
      }
    };
    ws.onclose = () => {
      this.ws = null;
      board.setState({ link: "offline" });
      if (this.stopped) return;
      // The server may still be starting (Electron launches it), so keep trying.
      const delay = Math.min(5000, 400 * 2 ** this.attempt++);
      this.reconnect = window.setTimeout(() => this.start(), delay);
    };
  }

  /** Close the connection and stop reconnecting (a hot-reloaded module would otherwise leave this one running). */
  stop(): void {
    this.stopped = true;
    if (this.reconnect !== null) window.clearTimeout(this.reconnect);
    this.reconnect = null;
    this.ws?.close();
  }

  spawn(role: Role, backend: Backend): void {
    this.send({ op: "spawn", role, backend });
  }
  command(ids: string[], text: string): void {
    this.send({ op: "command", ids, text });
  }
  approve(id: string): void {
    this.send({ op: "approve", id });
  }
  reject(id: string): void {
    this.send({ op: "reject", id });
  }
  retry(id: string): void {
    this.send({ op: "retry", id });
  }
  abandon(id: string): void {
    this.send({ op: "abandon", id });
  }
  setAutonomy(id: string, on: boolean): void {
    this.send({ op: "setAutonomy", id, on });
  }
  setRepo(repo: string): void {
    this.send({ op: "setRepo", repo });
  }
  setRemote(target: string, port: number | null, key: string | null): void {
    this.send({ op: "setRemote", target, port, key });
  }
  diff(id: string): void {
    this.send({ op: "diff", id });
  }
  merge(id: string): void {
    this.send({ op: "merge", id });
  }
  setCredential(name: CredentialName, value: string): void {
    this.send({ op: "setCredential", name, value });
  }
  clearCredential(name: CredentialName): void {
    this.send({ op: "clearCredential", name });
  }
  newVillage(): void {
    this.send({ op: "newVillage" });
  }

  private send(op: Record<string, unknown>): void {
    // Deliberately not queued while offline: replaying stale orders later would surprise the player.
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(op));
  }
}
