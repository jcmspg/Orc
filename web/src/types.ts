// Shared shapes for the board. The fake engine emits these events today; the
// Python backend will emit the same ones over a WebSocket later.

export type Role = "chieftain" | "coder" | "reviewer" | "tester";
export type Backend = "claude" | "cursor" | "copilot" | "codex";
export type OrcState =
  | "idle"
  | "thinking"
  | "working"
  | "walking"
  | "reviewing"
  | "awaiting"
  | "failed";
export type LogLevel = "info" | "cmd" | "ok" | "warn" | "error";

export interface LogLine {
  t: number;
  text: string;
  level: LogLevel;
}

export interface Pending {
  kind: "plan" | "commands";
  summary: string;
  items: string[];
}

export interface Point {
  x: number;
  y: number;
}

export interface Orc {
  id: string;
  name: string;
  role: Role;
  backend: Backend;
  autonomy: boolean;
  state: OrcState;
  task: string | null;
  hut: Point;
  log: LogLine[];
  pending: Pending | null;
}

export interface Edge {
  from: string;
  to: string;
  kind: "command" | "review";
}

export type OrcEvent =
  | { type: "orc.spawned"; orc: Orc }
  | { type: "edge.added"; edge: Edge }
  | { type: "orc.state"; id: string; state: OrcState; task?: string | null }
  | { type: "orc.log"; id: string; line: string; level?: LogLevel }
  | { type: "orc.say"; id: string; text: string }
  | { type: "orc.awaiting"; id: string; pending: Pending }
  | { type: "orc.resolved"; id: string }
  | { type: "orc.autonomy"; id: string; autonomy: boolean }
  | { type: "orders.sent"; from: string; to: string; label: string; durationMs: number }
  | { type: "crate.sent"; from: string; to: string; label: "branch" | "findings"; durationMs: number }
  | { type: "gold.delivered"; from: string; to: string; durationMs: number }
  | { type: "gold.banked"; amount: number };
