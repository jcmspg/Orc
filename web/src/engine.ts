// Simulated backend. It drives the full loop (chieftain plans, peons code in
// black boxes, reviewers approve or send work back, gold reaches the town
// hall) with timers and canned output, so the map can be built before the real
// Python engine exists. The real engine will implement the same `Engine`
// interface and emit the same events.

import { board, dispatch } from "./store";
import type { Backend, Edge, LogLevel, Orc, OrcState, Pending, Role } from "./types";

export interface Engine {
  spawn(role: Role, backend: Backend): void;
  command(ids: string[], text: string): void;
  approve(id: string): void;
  reject(id: string): void;
  retry(id: string): void;
  abandon(id: string): void;
  setAutonomy(id: string, on: boolean): void;
}

type Job = {
  kind: "plan" | "code" | "review" | "chat";
  task: string;
  from?: string;
  attempt: number;
};

const WALK_MS = 3200;

const NAMES = [
  "Durg", "Mogka", "Brakk", "Uzgash", "Rokhar", "Gruk", "Nazka", "Throg",
  "Borka", "Skarn", "Ulgra", "Dakka", "Morz", "Grisha", "Kruul", "Yarga",
];
const VOICES = ["Work work!", "Zug zug.", "Me on it.", "Ugh... fine.", "Yes, chief!", "Smash code!"];
const PLAN_VERBS = ["Build the core", "Wire it up", "Handle errors", "Polish"];
const FINDINGS = [
  "missing null check in the handler",
  "test name does not match the behaviour",
  "unused import left behind",
  "error swallowed in a catch block",
  "magic number should be a named constant",
];

const after = (ms: number, fn: () => void) => window.setTimeout(fn, ms);
const pick = <T>(xs: readonly T[]): T => xs[Math.floor(Math.random() * xs.length)];
const slug = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 24).replace(/^-+|-+$/g, "") || "task";

/** Hut positions: an inner ring of 6 around the town hall, then an outer ring of 10. */
function slot(i: number) {
  if (i < 6) {
    const a = -Math.PI / 2 + Math.PI / 6 + (i * Math.PI * 2) / 6;
    return { x: Math.round(Math.cos(a) * 300), y: Math.round(Math.sin(a) * 230) };
  }
  const a = ((i - 6) * Math.PI * 2) / 10;
  return { x: Math.round(Math.cos(a) * 540), y: Math.round(Math.sin(a) * 410) };
}

export class FakeEngine implements Engine {
  private seq = 0;
  private workers = 0;
  private queues = new Map<string, Job[]>();
  private gates = new Map<string, () => void>();
  private retries = new Map<string, () => void>();

  boot(): void {
    if (this.seq > 0) return;
    this.spawn("chieftain", "claude");
    this.spawn("coder", "claude");
    this.spawn("coder", "cursor");
    this.spawn("tester", "copilot");
    this.spawn("reviewer", "claude");
  }

  spawn(role: Role, backend: Backend): void {
    if (role === "chieftain" && this.hall()) return;
    const id = `orc-${++this.seq}`;
    const used = new Set(Object.values(board.getState().orcs).map((o) => o.name));
    const name = role === "chieftain" ? "Ozgar" : NAMES.find((n) => !used.has(n)) ?? `Peon ${this.seq}`;
    const orc: Orc = {
      id,
      name,
      role,
      backend,
      // The chieftain starts supervised so the approval flow is visible.
      autonomy: role !== "chieftain",
      state: "idle",
      task: null,
      hut: role === "chieftain" ? { x: 0, y: 0 } : slot(this.workers++),
      log: [],
      pending: null,
    };
    dispatch({ type: "orc.spawned", orc });
    this.queues.set(id, []);

    const hall = this.hall();
    if (role !== "chieftain" && hall) this.link(hall.id, id, "command");
    if (role === "coder" || role === "tester") {
      for (const r of this.byRole("reviewer")) this.link(id, r.id, "review");
    }
    if (role === "reviewer") {
      for (const c of [...this.byRole("coder"), ...this.byRole("tester")]) this.link(c.id, id, "review");
    }

    this.log(id, `${name} the ${role} reporting for duty (${backend}).`);
    dispatch({ type: "orc.say", id, text: "Ready to work!" });
  }

  command(ids: string[], text: string): void {
    for (const id of ids) {
      const orc = this.get(id);
      if (!orc) continue;
      const kind = orc.role === "chieftain" ? "plan" : orc.role === "reviewer" ? "chat" : "code";
      dispatch({ type: "orc.say", id, text: pick(VOICES) });
      this.enqueue(id, { kind, task: text, attempt: 0 });
    }
  }

  approve(id: string): void {
    const go = this.gates.get(id);
    if (!go) return;
    this.gates.delete(id);
    dispatch({ type: "orc.resolved", id });
    dispatch({ type: "orc.say", id, text: "Zug zug." });
    this.log(id, "Orders approved.", "ok");
    go();
  }

  reject(id: string): void {
    if (!this.gates.delete(id)) return;
    dispatch({ type: "orc.resolved", id });
    this.log(id, "Orders rejected. Standing down.", "warn");
    this.next(id);
  }

  retry(id: string): void {
    const fn = this.retries.get(id);
    if (!fn) return;
    this.retries.delete(id);
    this.log(id, "Trying again in a fresh black box.");
    fn();
  }

  abandon(id: string): void {
    if (!this.retries.delete(id)) return;
    this.log(id, "Task abandoned. Black box destroyed.", "warn");
    this.next(id);
  }

  setAutonomy(id: string, on: boolean): void {
    dispatch({ type: "orc.autonomy", id, autonomy: on });
    this.log(id, on ? "Autonomy on: acting without asking." : "Autonomy off: will ask before acting.");
    if (on && this.gates.has(id)) this.approve(id);
  }

  // --- job flow ---

  private enqueue(id: string, job: Job): void {
    const queue = this.queues.get(id);
    const orc = this.get(id);
    if (!queue || !orc) return;
    queue.push(job);
    if (orc.state === "idle") this.next(id);
    else this.log(id, `Queued: ${job.task}`);
  }

  private next(id: string): void {
    const job = this.queues.get(id)?.shift();
    if (!job) {
      this.setState(id, "idle", null);
      return;
    }
    if (job.kind === "plan") this.plan(id, job);
    else if (job.kind === "code") this.code(id, job);
    else if (job.kind === "review") this.review(id, job);
    else this.chat(id, job);
  }

  /** Run `go` now if the orc is autonomous, otherwise park it until approved. */
  private gate(id: string, pending: Pending, go: () => void): void {
    if (this.get(id)?.autonomy) {
      go();
      return;
    }
    this.log(id, `Awaiting your orders: ${pending.summary}.`, "warn");
    dispatch({ type: "orc.awaiting", id, pending });
    this.gates.set(id, go);
  }

  private plan(id: string, job: Job): void {
    this.setState(id, "thinking", job.task);
    this.log(id, `Studying the war map: "${job.task}"`);
    after(2200, () => {
      const workers = this.commandTargets(id).filter((o) => o.role === "coder" || o.role === "tester");
      if (!workers.length) {
        this.log(id, "No peons to command. Train some coders first.", "warn");
        this.next(id);
        return;
      }
      const orders = workers.map((w, i) => ({
        w,
        task: w.role === "tester" ? `Write tests: ${job.task}` : `${PLAN_VERBS[i % PLAN_VERBS.length]}: ${job.task}`,
      }));
      const pending: Pending = {
        kind: "plan",
        summary: `Split the job into ${orders.length} tasks`,
        items: orders.map((o) => `${o.w.name} (${o.w.role}): ${o.task}`),
      };
      this.gate(id, pending, () => {
        this.setState(id, "working", job.task);
        orders.forEach(({ w, task }, i) =>
          after(i * 350, () => {
            dispatch({ type: "orders.sent", from: id, to: w.id, label: task, durationMs: 900 });
            after(900, () => {
              dispatch({ type: "orc.say", id: w.id, text: pick(VOICES) });
              this.enqueue(w.id, { kind: "code", task, from: id, attempt: 0 });
            });
          }),
        );
        after(orders.length * 350 + 900, () => {
          this.log(id, `Orders delivered to ${orders.length} peons.`, "ok");
          this.next(id);
        });
      });
    });
  }

  private code(id: string, job: Job): void {
    const fixing = job.attempt > 0;
    const title = fixing ? `Fix review findings: ${job.task}` : job.task;
    this.setState(id, "thinking", title);
    this.log(id, fixing ? "Reading the reviewer's notes..." : "Cloning the repo into a fresh black box...");
    after(1500, () => {
      const orc = this.get(id);
      if (!orc) return;
      const branch = `orc/${slug(orc.name)}/${slug(job.task)}`;
      const file = orc.role === "tester" ? `tests/${slug(job.task)}.test.ts` : `src/${slug(job.task)}.ts`;
      const cmds = fixing
        ? [`git switch ${branch}`, `edit ${file}`, "npm test", 'git commit -am "address review"']
        : [`git switch -c ${branch}`, "npm ci", `edit ${file}`, "npm test", `git commit -am "${job.task.slice(0, 40)}"`];
      const pending: Pending = {
        kind: "commands",
        summary: `Run ${cmds.length} commands in the black box`,
        items: cmds,
      };
      this.gate(id, pending, () => {
        this.setState(id, "working", title);
        cmds.forEach((c, i) => after(i * 1000, () => this.log(id, `$ ${c}`, "cmd")));
        after(cmds.length * 1000 + 300, () => {
          if (!fixing && Math.random() < 0.15) {
            this.setState(id, "failed", title);
            this.log(id, "npm test exited with code 1 (2 failing). Black box kept for inspection.", "error");
            dispatch({ type: "orc.say", id, text: "Ugh. It broke." });
            this.retries.set(id, () => this.code(id, job));
            return;
          }
          this.log(id, `Exported ${branch} from the black box.`, "ok");
          const reviewer = this.reviewerFor(id);
          if (!reviewer) {
            this.deliverGold(id, () => this.next(id));
            return;
          }
          this.setState(id, "walking", title);
          dispatch({ type: "crate.sent", from: id, to: reviewer.id, label: "branch", durationMs: WALK_MS });
          after(WALK_MS / 2, () =>
            this.enqueue(reviewer.id, { kind: "review", task: job.task, from: id, attempt: job.attempt }),
          );
          after(WALK_MS, () => this.next(id));
        });
      });
    });
  }

  private review(id: string, job: Job): void {
    const author = job.from ? this.get(job.from) : undefined;
    if (!author) {
      this.next(id);
      return;
    }
    this.setState(id, "reviewing", `Review: ${job.task}`);
    this.log(id, `Reading ${author.name}'s diff...`);
    after(3000, () => {
      if (job.attempt === 0 && Math.random() < 0.4) {
        this.log(id, `Changes requested: ${pick(FINDINGS)}.`, "warn");
        this.setState(id, "walking");
        dispatch({ type: "crate.sent", from: id, to: author.id, label: "findings", durationMs: WALK_MS });
        after(WALK_MS / 2, () =>
          this.enqueue(author.id, { kind: "code", task: job.task, from: id, attempt: job.attempt + 1 }),
        );
        after(WALK_MS, () => this.next(id));
        return;
      }
      this.log(id, `Approved ${author.name}'s work. Hauling gold to the town hall.`, "ok");
      this.deliverGold(id, () => this.next(id));
    });
  }

  private chat(id: string, job: Job): void {
    this.setState(id, "thinking", job.task);
    after(1800, () => {
      this.log(id, `Nothing to review for "${job.task}" yet. Me review what coders bring.`);
      this.next(id);
    });
  }

  private deliverGold(id: string, done: () => void): void {
    const hall = this.hall();
    if (!hall) {
      dispatch({ type: "gold.banked", amount: 1 });
      done();
      return;
    }
    this.setState(id, "walking");
    dispatch({ type: "gold.delivered", from: id, to: hall.id, durationMs: WALK_MS });
    after(WALK_MS / 2, () => dispatch({ type: "gold.banked", amount: 1 }));
    after(WALK_MS, done);
  }

  // --- helpers ---

  private get(id: string): Orc | undefined {
    return board.getState().orcs[id];
  }

  private hall(): Orc | undefined {
    return this.byRole("chieftain")[0];
  }

  private byRole(role: Role): Orc[] {
    return Object.values(board.getState().orcs).filter((o) => o.role === role);
  }

  private commandTargets(id: string): Orc[] {
    const { edges, orcs } = board.getState();
    return edges.filter((e) => e.from === id && e.kind === "command").map((e) => orcs[e.to]).filter(Boolean);
  }

  private reviewerFor(id: string): Orc | undefined {
    const { edges, orcs } = board.getState();
    const reviewers = edges.filter((e) => e.from === id && e.kind === "review").map((e) => orcs[e.to]).filter(Boolean);
    const load = (o: Orc) => (this.queues.get(o.id)?.length ?? 0) + (o.state === "idle" ? 0 : 1);
    return reviewers.sort((a, b) => load(a) - load(b))[0];
  }

  private link(from: string, to: string, kind: Edge["kind"]): void {
    const exists = board.getState().edges.some((e) => e.from === from && e.to === to && e.kind === kind);
    if (!exists) dispatch({ type: "edge.added", edge: { from, to, kind } });
  }

  private setState(id: string, state: OrcState, task?: string | null): void {
    dispatch({ type: "orc.state", id, state, task });
  }

  private log(id: string, line: string, level: LogLevel = "info"): void {
    dispatch({ type: "orc.log", id, line, level });
  }
}

export const engine = new FakeEngine();
