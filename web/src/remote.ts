// Engine that talks to the Python server (`orc serve`) over a WebSocket.
// Server events are applied to the board as-is; HUD actions become ops.

import { board, dispatch } from "./store";
import type { Backend, CredentialName, Engine, OrcEvent, Role } from "./types";

export class RemoteEngine implements Engine {
  private ws: WebSocket | null = null;
  private attempt = 0;

  constructor(private readonly url: string) {}

  start(): void {
    if (this.ws) return;
    board.setState({ link: "connecting" });
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      board.setState({ link: "live" });
    };
    ws.onmessage = (m) => dispatch(JSON.parse(m.data as string) as OrcEvent);
    ws.onclose = () => {
      this.ws = null;
      board.setState({ link: "offline" });
      // The server may still be starting (Electron launches it), so keep trying.
      const delay = Math.min(5000, 400 * 2 ** this.attempt++);
      window.setTimeout(() => this.start(), delay);
    };
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
  setCredential(name: CredentialName, value: string): void {
    this.send({ op: "setCredential", name, value });
  }
  clearCredential(name: CredentialName): void {
    this.send({ op: "clearCredential", name });
  }

  private send(op: Record<string, unknown>): void {
    // Deliberately not queued while offline: replaying stale orders later would surprise the player.
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(op));
  }
}
