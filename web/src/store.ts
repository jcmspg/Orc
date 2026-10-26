import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import type { CredentialName, CredentialStatus, Edge, LogLine, Orc, OrcEvent } from "./types";

export interface FeedLine extends LogLine {
  orcId: string;
  name: string;
}

export type Link = "sim" | "connecting" | "live" | "offline";
export type MenuView = "main" | "settings" | "controls" | "quit";

export interface BoardState {
  orcs: Record<string, Orc>;
  order: string[];
  edges: Edge[];
  gold: number;
  selected: string[];
  feed: FeedLine[];
  repo: string | null;
  link: Link;
  /** Which credentials the engine has (never their values); null until it reports. */
  credentials: Record<CredentialName, CredentialStatus> | null;
  credentialError: string | null;
  menu: MenuView | null;
}

const EMPTY = { orcs: {}, order: [], edges: [], gold: 0, selected: [], feed: [], repo: null };

export const board = createStore<BoardState>(() => ({
  ...EMPTY,
  link: "connecting",
  credentials: null,
  credentialError: null,
  menu: null,
}));

export function openMenu(view: MenuView | null): void {
  board.setState({ menu: view });
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
    default:
      return s;
  }
}
