// Simulated engine: drives the full loop (chieftain plans, peons code,
// reviewers approve or send work back, gold reaches the town hall) with timers
// and canned output, entirely in the app. Used with `?sim` for demos and UI
// work without the Python server. The real engine (`orc serve`) emits the same
// events.

import { board, dispatch } from "./store";
import type { Backend, Edge, Engine, LogLevel, Orc, OrcState, Pending, Role } from "./types";

type Job = {
  kind: "plan" | "code" | "review" | "chat";
  task: string;
  from?: string;
  attempt: number;
  work?: SimWork;
};

/** One simulated branch of work, from first commit to delivery. */
type SimWork = {
  id: string;
  owner: string;
  task: string;
  branch: string;
  file: string;
  tester: boolean;
  /** Also edits the shared src/index.ts, so two such branches conflict. */
  touchesIndex: boolean;
  fixes: number;
};

/** What the simulated repo knows about a delivered branch. */
type SimBranch = { work: SimWork; diff: string; mergedInto: string | null };

// The simulated repo has `main` checked out and a free `develop` branch, so the
// simulation shows both outcomes of an in-app merge.
const SIM_CHECKOUT = "/home/you/war-target";

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

const fakeSha = () =>
  Array.from({ length: 40 }, () => "0123456789abcdef"[Math.floor(Math.random() * 16)]).join("");

const camel = (s: string) =>
  slug(s)
    .split("-")
    .map((w, i) => (i ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join("") || "task";

/** A unified diff that looks like what a peon would plausibly have written for the task. */
function fakeDiff(work: SimWork): string {
  const name = camel(work.task.replace(/^[^:]*:\s*/, ""));
  const out: string[] = [];
  const newFile = (path: string, lines: string[]) => {
    out.push(
      `diff --git a/${path} b/${path}`,
      "new file mode 100644",
      `index 0000000..${fakeSha().slice(0, 7)}`,
      "--- /dev/null",
      `+++ b/${path}`,
      `@@ -0,0 +1,${lines.length} @@`,
      ...lines.map((l) => `+${l}`),
    );
  };
  if (work.tester) {
    newFile(work.file, [
      `import { describe, expect, it } from "vitest";`,
      `import { ${name} } from "../src/${slug(work.task.replace(/^[^:]*:\s*/, ""))}";`,
      "",
      `describe("${name}", () => {`,
      `  it("accepts valid input", () => {`,
      `    expect(${name}({ value: "ok" })).toEqual({ ok: true });`,
      "  });",
      "",
      `  it("rejects empty input", () => {`,
      `    expect(${name}({ value: "" }).ok).toBe(false);`,
      "  });",
      ...(work.fixes ? ["", `  it("trims whitespace", () => {`, `    expect(${name}({ value: "  ok " }).ok).toBe(true);`, "  });"] : []),
      "});",
    ]);
  } else {
    newFile(work.file, [
      `export interface ${name[0].toUpperCase()}${name.slice(1)}Input {`,
      "  value: string;",
      "}",
      "",
      `export function ${name}(input: ${name[0].toUpperCase()}${name.slice(1)}Input): { ok: boolean; error?: string } {`,
      ...(work.fixes ? ["  if (input == null) return { ok: false, error: \"missing input\" };"] : []),
      "  const value = input.value.trim();",
      "  if (!value) {",
      `    return { ok: false, error: "value is required" };`,
      "  }",
      "  return { ok: true };",
      "}",
    ]);
  }
  if (work.touchesIndex) {
    out.push(
      "diff --git a/src/index.ts b/src/index.ts",
      `index ${fakeSha().slice(0, 7)}..${fakeSha().slice(0, 7)} 100644`,
      "--- a/src/index.ts",
      "+++ b/src/index.ts",
      "@@ -1,6 +1,7 @@",
      ` export { createApp } from "./app";`,
      ` export { router } from "./router";`,
      `-export const VERSION = "0.3.0";`,
      `+export { ${name} } from "./${slug(work.task.replace(/^[^:]*:\s*/, ""))}";`,
      `+export const VERSION = "0.3.1";`,
      " ",
      " export default createApp;",
    );
  }
  return out.join("\n") + "\n";
}

/** `git diff --shortstat` for a diff. */
function shortstat(diff: string): string {
  const lines = diff.split("\n");
  const files = lines.filter((l) => l.startsWith("diff --git ")).length;
  const plus = lines.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length;
  const minus = lines.filter((l) => l.startsWith("-") && !l.startsWith("---")).length;
  const s = (n: number) => (n === 1 ? "" : "s");
  return `${files} file${s(files)} changed, ${plus} insertion${s(plus)}(+)` + (minus ? `, ${minus} deletion${s(minus)}(-)` : "");
}

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
  private branches = new Map<string, SimBranch>();
  private deliveredCount = 0;

  start(): void {
    board.setState({ link: "sim" });
    if (this.seq > 0) return;
    dispatch({ type: "board.repo", repo: "(simulated)" });
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

  setRepo(repo: string): void {
    dispatch({ type: "board.repo", repo });
    const hall = this.hall();
    if (hall) this.log(hall.id, `War target set: ${repo} (simulated, nothing is touched)`, "ok");
  }

  // The simulation runs no agents, so it has no use for credentials.
  setCredential(): void {}
  clearCredential(): void {}

  setAutonomy(id: string, on: boolean): void {
    dispatch({ type: "orc.autonomy", id, autonomy: on });
    this.log(id, on ? "Autonomy on: acting without asking." : "Autonomy off: will ask before acting.");
    if (on && this.gates.has(id)) this.approve(id);
  }

  // --- spoils: delivered branches ---

  diff(id: string): void {
    const b = this.branches.get(id);
    if (!b) return;
    after(250, () => dispatch({ type: "branch.diff", id, text: b.diff, truncated: false, error: null }));
  }

  merge(id: string): void {
    const b = this.branches.get(id);
    const d = board.getState().deliveries.find((x) => x.id === id);
    if (!b || !d || d.status === "merged") return;
    const into = d.baseBranch ?? "main";
    const manual = `git merge --no-ff ${d.branch}`;
    // Branches already merged into the same target that also edited src/index.ts.
    const clash = (target: string) =>
      b.work.touchesIndex &&
      [...this.branches.values()].some((o) => o !== b && o.mergedInto === target && o.work.touchesIndex);
    after(600, () => {
      if (into === "main") {
        const conflicts = clash("main") ? ["src/index.ts"] : [];
        const warn = conflicts.length ? ` It will conflict in ${conflicts.join(", ")}.` : "";
        dispatch({
          type: "branch.status",
          id,
          status: conflicts.length ? "conflict" : "delivered",
          note: `main is checked out in ${SIM_CHECKOUT}, and orc never touches a working tree.${warn} Merge it there yourself:`,
          command: manual,
          conflicts,
        });
        this.hallLog(`Left ${d.branch} for you: main is checked out in ${SIM_CHECKOUT}. Run: ${manual}`, "warn");
      } else if (clash(into)) {
        dispatch({
          type: "branch.status",
          id,
          status: "conflict",
          note: `Merging into ${into} would conflict in 1 file(s). Nothing was changed. Resolve it yourself:`,
          command: `git switch ${into} && ${manual}`,
          conflicts: ["src/index.ts"],
        });
        this.hallLog(`${d.branch} conflicts with ${into} in src/index.ts. Nothing was merged.`, "warn");
      } else {
        b.mergedInto = into;
        const commit = fakeSha();
        dispatch({ type: "branch.merged", id, into, commit });
        this.hallLog(`Merged ${d.branch} into ${into} (${commit.slice(0, 8)}).`, "ok");
        const hall = this.hall();
        if (hall) dispatch({ type: "orc.say", id: hall.id, text: "More gold for the horde!" });
      }
    });
  }

  private newWork(orc: Orc, task: string): SimWork {
    const id = fakeSha().slice(0, 6);
    const tester = orc.role === "tester";
    return {
      id,
      owner: orc.id,
      task,
      branch: `orc/${slug(orc.name)}/${slug(task)}-${id}`,
      file: tester ? `tests/${slug(task)}.test.ts` : `src/${slug(task)}.ts`,
      tester,
      touchesIndex: !tester && Math.random() < 0.5,
      fixes: 0,
    };
  }

  private deliver(work: SimWork): void {
    // Every third delivery was started from `main` (checked out), the rest from `develop` (free).
    const baseBranch = this.deliveredCount++ % 3 === 0 ? "main" : "develop";
    const diff = fakeDiff(work);
    this.branches.set(work.id, { work, diff, mergedInto: null });
    dispatch({
      type: "branch.delivered",
      delivery: {
        id: work.id,
        branch: work.branch,
        task: work.task,
        owner: work.owner,
        repo: board.getState().repo ?? SIM_CHECKOUT,
        base: fakeSha(),
        baseBranch,
        head: fakeSha(),
        stat: shortstat(diff),
        status: "delivered",
        note: null,
        command: null,
        conflicts: [],
        mergeCommit: null,
        bundle: null,
        at: Date.now(),
      },
    });
    const where = `Branch ${work.branch} is in your repo. Look it over and merge it from the spoils.`;
    this.log(work.owner, where, "ok");
    this.hallLog(`Gold! ${where}`, "ok");
  }

  private hallLog(line: string, level: LogLevel): void {
    const hall = this.hall();
    if (hall) this.log(hall.id, line, level);
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
      const work = (job.work ??= this.newWork(orc, job.task));
      if (fixing) work.fixes = job.attempt;
      const { branch, file } = work;
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
            this.deliverGold(id, work, () => this.next(id));
            return;
          }
          this.setState(id, "walking", title);
          dispatch({ type: "crate.sent", from: id, to: reviewer.id, label: "branch", durationMs: WALK_MS });
          after(WALK_MS / 2, () =>
            this.enqueue(reviewer.id, { kind: "review", task: job.task, from: id, attempt: job.attempt, work }),
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
          this.enqueue(author.id, { kind: "code", task: job.task, from: id, attempt: job.attempt + 1, work: job.work }),
        );
        after(WALK_MS, () => this.next(id));
        return;
      }
      this.log(id, `Approved ${author.name}'s work. Hauling gold to the town hall.`, "ok");
      this.deliverGold(id, job.work, () => this.next(id));
    });
  }

  private chat(id: string, job: Job): void {
    this.setState(id, "thinking", job.task);
    after(1800, () => {
      this.log(id, `Nothing to review for "${job.task}" yet. Me review what coders bring.`);
      this.next(id);
    });
  }

  private deliverGold(id: string, work: SimWork | undefined, done: () => void): void {
    const hall = this.hall();
    if (work) this.deliver(work);
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
