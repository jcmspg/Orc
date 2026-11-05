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

/**
 * delivered: in the repo, unmerged. bundled: remote war target, only a bundle exists.
 * merged. conflict: a merge would conflict. missing / moved: deleted or changed outside orc.
 */
export type DeliveryStatus = "delivered" | "bundled" | "merged" | "conflict" | "missing" | "moved";

/** A finished branch: the gold. */
export interface Delivery {
  id: string;
  branch: string;
  task: string;
  /** Orc id of the coder that made it. */
  owner: string;
  repo: string;
  /** Commit the work started from. */
  base: string;
  /** Branch HEAD was on when the work started: where it merges back. Null for a detached HEAD. */
  baseBranch: string | null;
  /** Commit delivered. */
  head: string;
  /** `git diff --shortstat` summary. */
  stat: string;
  status: DeliveryStatus;
  /** Plain explanation of the status or the last merge attempt. */
  note: string | null;
  /** A git command for the player to run, when orc won't do it. */
  command: string | null;
  conflicts: string[];
  mergeCommit: string | null;
  bundle: string | null;
  /** Delivery time, ms since epoch. */
  at: number;
}

/**
 * The war target when it is a repo on another machine, reached with ssh. `key` is the *name* of a
 * file in the player's ~/.ssh (never a path or its contents); `port` and `key` are null for defaults.
 */
export interface Remote {
  /** `user@host:path`, the same string as `board.repo`. */
  target: string;
  port: number | null;
  key: string | null;
}

/** Agent credentials the engine can save for black boxes. Values never reach the app. */
export type CredentialName =
  | "CLAUDE_CODE_OAUTH_TOKEN"
  | "ANTHROPIC_API_KEY"
  | "CURSOR_API_KEY"
  | "CODEX_API_KEY"
  | "COPILOT_GITHUB_TOKEN";
export type CredentialStatus = "env" | "saved" | "missing";

export type OrcEvent =
  | { type: "app.credentials"; status: Record<CredentialName, CredentialStatus>; error?: string }
  | { type: "board.reset" }
  | { type: "board.repo"; repo: string | null }
  | { type: "board.remote"; remote: Remote | null }
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
  | { type: "gold.banked"; amount: number }
  | { type: "branch.delivered"; delivery: Delivery }
  | {
      type: "branch.status";
      id: string;
      status: DeliveryStatus;
      note: string | null;
      command: string | null;
      conflicts: string[];
    }
  | { type: "branch.merged"; id: string; into: string; commit: string }
  | { type: "branch.diff"; id: string; text: string; truncated: boolean; error: string | null };

/** What the HUD can ask of an engine (simulated or the real server). */
export interface Engine {
  start(): void;
  spawn(role: Role, backend: Backend): void;
  command(ids: string[], text: string): void;
  approve(id: string): void;
  reject(id: string): void;
  retry(id: string): void;
  abandon(id: string): void;
  setAutonomy(id: string, on: boolean): void;
  setRepo(repo: string): void;
  /** Point the war target at a repo over ssh. The engine validates everything and answers with `board.repo` + `board.remote`, or a log line. */
  setRemote(target: string, port: number | null, key: string | null): void;
  /** Ask for a delivery's diff; it arrives as a `branch.diff` event. */
  diff(id: string): void;
  /** Merge a delivery into its base branch if that's safe; the outcome arrives as `branch.merged` or `branch.status`. */
  merge(id: string): void;
  setCredential(name: CredentialName, value: string): void;
  clearCredential(name: CredentialName): void;
  /** Stop all work, destroy every black box and found the default village (starts with `board.reset`). */
  newVillage(): void;
}
