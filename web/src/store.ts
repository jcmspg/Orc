import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import type { CredentialName, CredentialStatus, Delivery, Edge, LogLine, Orc, OrcEvent } from "./types";

export interface FeedLine extends LogLine {
  orcId: string;
  name: string;
}

export type Link = "sim" | "connecting" | "live" | "offline";
export type MenuView = "main" | "settings" | "controls" | "quit";

/** The engine's answer to a diff request. */
export interface DiffReply {
  text: string;
  truncated: boolean;
  error: string | null;
}

export interface BoardState {
  orcs: Record<string, Orc>;
  order: string[];
  edges: Edge[];
  gold: number;
  selected: string[];
  feed: FeedLine[];
  repo: string | null;
  /** Delivered branches, oldest first. */
  deliveries: Delivery[];
  /** Latest diff reply per delivery id. */
  diffs: Record<string, DiffReply>;
  link: Link;
  /** Which credentials the engine has (never their values); null until it reports. */
  credentials: Record<CredentialName, CredentialStatus> | null;
  credentialError: string | null;
  menu: MenuView | null;
  /** Spoils panel open. */
  spoils: boolean;
  /** Delivery whose diff is on screen. */
  viewing: string | null;
}

const EMPTY = { orcs: {}, order: [], edges: [], gold: 0, selected: [], feed: [], repo: null, deliveries: [], diffs: {} };

export const board = createStore<BoardState>(() => ({
  ...EMPTY,
  link: "connecting",
  credentials: null,
  credentialError: null,
  menu: null,
  spoils: false,
  viewing: null,
}));

export function openMenu(view: MenuView | null): void {
  board.setState({ menu: view });
}

export function toggleSpoils(open?: boolean): void {
  board.setState((s) => ({ spoils: open ?? !s.spoils }));
}

export function viewDiff(id: string | null): void {
  board.setState({ viewing: id });
}

function patchDelivery(s: BoardState, id: string, patch: Partial<Delivery>): BoardState {
  if (!s.deliveries.some((d) => d.id === id)) return s;
  return { ...s, deliveries: s.deliveries.map((d) => (d.id === id ? { ...d, ...patch } : d)) };
}

export const useBoard = <T>(selector: (s: BoardState) => T): T => useStore(board, selector);

const listeners = new Set<(e: OrcEvent) => void>();

/** Subscribe to raw events (the map uses these to trigger animations). */
export function onEvent(fn: (e: OrcEvent) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Apply an event to the board, then fan it out to listeners. */
export function dispatch(e: OrcEvent): void {
  board.setState((s) => reduce(s, e));
  listeners.forEach((fn) => fn(e));
}

export function select(ids: string[]): void {
  board.setState({ selected: ids });
}

function patchOrc(s: BoardState, id: string, patch: Partial<Orc>): BoardState {
  const orc = s.orcs[id];
  if (!orc) return s;
  return { ...s, orcs: { ...s.orcs, [id]: { ...orc, ...patch } } };
}

function reduce(s: BoardState, e: OrcEvent): BoardState {
  switch (e.type) {
    case "app.credentials":
      return { ...s, credentials: e.status, credentialError: e.error ?? null };
    case "board.reset":
      return { ...s, ...EMPTY };
    case "board.repo":
      return { ...s, repo: e.repo };
    case "orc.spawned":
      return { ...s, orcs: { ...s.orcs, [e.orc.id]: e.orc }, order: [...s.order, e.orc.id] };
    case "edge.added":
      return { ...s, edges: [...s.edges, e.edge] };
    case "orc.state":
      return patchOrc(s, e.id, {
        state: e.state,
        ...(e.task !== undefined ? { task: e.task } : {}),
      });
    case "orc.log": {
      const orc = s.orcs[e.id];
      if (!orc) return s;
      const line: LogLine = { t: Date.now(), text: e.line, level: e.level ?? "info" };
      const next = patchOrc(s, e.id, { log: [...orc.log, line].slice(-200) });
      return { ...next, feed: [...s.feed, { ...line, orcId: e.id, name: orc.name }].slice(-100) };
    }
    case "orc.awaiting":
      return patchOrc(s, e.id, { state: "awaiting", pending: e.pending });
    case "orc.resolved":
      return patchOrc(s, e.id, { pending: null });
    case "orc.autonomy":
      return patchOrc(s, e.id, { autonomy: e.autonomy });
    case "gold.banked":
      return { ...s, gold: s.gold + e.amount };
    case "branch.delivered": {
      const others = s.deliveries.filter((d) => d.id !== e.delivery.id);
      return { ...s, deliveries: [...others, e.delivery] };
    }
    case "branch.status":
      return patchDelivery(s, e.id, { status: e.status, note: e.note, command: e.command, conflicts: e.conflicts });
    case "branch.merged":
      return patchDelivery(s, e.id, { status: "merged", mergeCommit: e.commit, note: null, command: null, conflicts: [] });
    case "branch.diff":
      return { ...s, diffs: { ...s.diffs, [e.id]: { text: e.text, truncated: e.truncated, error: e.error } } };
    default:
      return s;
  }
}
