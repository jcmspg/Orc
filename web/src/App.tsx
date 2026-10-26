import { useEffect, useRef, useState, type FormEvent } from "react";
import { engine } from "./engines";
import { GameView } from "./game/GameView";
import { select, useBoard, type Link } from "./store";
import type { Backend, Orc, OrcState, Role } from "./types";

const STATE_LABEL: Record<OrcState, string> = {
  idle: "Idle",
  thinking: "Thinking",
  working: "Working",
  walking: "Hauling",
  reviewing: "Reviewing",
  awaiting: "Awaiting orders",
  failed: "Failed",
};
const ROLE_LABEL: Record<Role, string> = {
  chieftain: "Chieftain",
  coder: "Coder",
  reviewer: "Reviewer",
  tester: "Tester",
};
const BACKENDS: Backend[] = ["claude", "cursor", "copilot", "codex"];
const LINK_LABEL: Record<Link, string> = {
  sim: "simulated",
  connecting: "connecting...",
  live: "engine live",
  offline: "engine offline",
};
const TRAINABLE: Role[] = ["coder", "reviewer", "tester"];

export default function App() {
  return (
    <div className="app">
      <GameView />
      <TopBar />
      <Barracks />
      <UnitPanel />
      <WarLog />
      <CommandBar />
    </div>
  );
}

function TopBar() {
  const orcs = useBoard((s) => s.orcs);
  const gold = useBoard((s) => s.gold);
  const link = useBoard((s) => s.link);
  const list = Object.values(orcs);
  const busy = list.filter((o) => o.state !== "idle").length;
  const waiting = list.filter((o) => o.state === "awaiting").length;
  const failed = list.filter((o) => o.state === "failed").length;
  return (
    <div className="topbar panel">
      <span className="brand">ORC</span>
      <span className="gold">{gold} gold</span>
      <span>{list.length} peons</span>
      <span>{busy} busy</span>
      {waiting > 0 && <span className="warn">{waiting} awaiting orders</span>}
      {failed > 0 && <span className="bad">{failed} failed</span>}
      <span className={`badge link-${link}`}>{LINK_LABEL[link]}</span>
    </div>
  );
}

/** Runtimes the real engine can run inside black boxes (the simulation fakes them all). */
const BOX_READY: Record<Backend, boolean> = { claude: true, cursor: true, copilot: false, codex: false };

function Barracks() {
  const [backend, setBackend] = useState<Backend>("claude");
  const sim = useBoard((s) => s.link === "sim");
  return (
    <div className="barracks panel">
      <h2>Barracks</h2>
      {TRAINABLE.map((role) => (
        <button key={role} onClick={() => engine.spawn(role, backend)}>
          <span className={`dot r-${role}`} /> Train {ROLE_LABEL[role]}
        </button>
      ))}
      <label className="field">
        Runtime
        <select value={backend} onChange={(e) => setBackend(e.target.value as Backend)}>
          {BACKENDS.map((b) => (
            <option key={b} value={b} disabled={!sim && !BOX_READY[b]}>
              {sim || BOX_READY[b] ? b : `${b} (not yet)`}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function UnitPanel() {
  const selected = useBoard((s) => s.selected);
  const orcs = useBoard((s) => s.orcs);
  const link = useBoard((s) => s.link);
  const repo = useBoard((s) => s.repo);
  const units = selected.map((id) => orcs[id]).filter(Boolean);

  if (link === "offline" || (link === "connecting" && !units.length)) {
    return (
      <div className="unit panel compact">
        <h2>{link === "offline" ? "Engine offline" : "Connecting to the engine..."}</h2>
        <p className="hint">
          The desktop app starts <code>orc serve</code> for you. If it isn't running, start it from the repo with{" "}
          <code>.venv/bin/orc serve</code>. To try orc without it, open the app with <code>?sim</code>.
        </p>
      </div>
    );
  }

  if (!units.length) {
    return (
      <div className="unit panel compact">
        {!repo && (
          <p className="pending">
            No war target yet. Select <b>Ozgar</b> and set the repo the peons should work on.
          </p>
        )}
        <h2>No peon selected</h2>
        <p className="hint">
          Click a peon or drag a box around several. Shift-click adds to the selection. Right-drag or WASD pans,
          scroll zooms, middle-drag or Q/E rotates, H recentres, Enter gives orders, Esc deselects. F11 fullscreen.
        </p>
        <p className="hint">
          Order <b>Ozgar</b> (the chieftain) to split a job across the peons. His autonomy is off, so he will ask
          before sending orders.
        </p>
      </div>
    );
  }

  if (units.length > 1) {
    return (
      <div className="unit panel">
        <h2>{units.length} peons selected</h2>
        <ul className="roster">
          {units.map((o) => (
            <li key={o.id} onClick={() => select([o.id])}>
              <span className={`dot s-${o.state}`} />
              <b>{o.name}</b>
              <span className="muted">{ROLE_LABEL[o.role]}</span>
              <span className="muted right">{STATE_LABEL[o.state]}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return <UnitDetail orc={units[0]} />;
}

function UnitDetail({ orc }: { orc: Orc }) {
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [orc.log.length, orc.id]);

  return (
    <div className="unit panel">
      <div className="unit-head">
        <h2>{orc.name}</h2>
        <span className={`role r-${orc.role}`}>{ROLE_LABEL[orc.role]}</span>
      </div>
      <div className="meta">
        <span>
          Runtime <b>{orc.backend}</b>
        </span>
        <span className={`pill s-${orc.state}`}>{STATE_LABEL[orc.state]}</span>
      </div>
      {orc.task && <p className="task">{orc.task}</p>}
      {orc.role === "chieftain" && <WarTarget />}

      <label className="toggle">
        <input type="checkbox" checked={orc.autonomy} onChange={(e) => engine.setAutonomy(orc.id, e.target.checked)} />
        <span className="track">
          <span className="knob" />
        </span>
        Autonomy {orc.autonomy ? "on" : "off"}
      </label>
      <p className="hint">{orc.autonomy ? "Acts on its own and reports back." : "Asks before every plan or batch of commands."}</p>

      {orc.pending && (
        <div className="pending">
          <h3>{orc.pending.kind === "plan" ? "Battle plan" : "Commands to run"}</h3>
          <p>{orc.pending.summary}</p>
          <ul>
            {orc.pending.items.map((item, i) => (
              <li key={i}>
                <code>{item}</code>
              </li>
            ))}
          </ul>
          <div className="row">
            <button className="ok" onClick={() => engine.approve(orc.id)}>
              Approve
            </button>
            <button onClick={() => engine.reject(orc.id)}>Reject</button>
          </div>
        </div>
      )}

      {orc.state === "failed" && (
        <div className="row">
          <button className="ok" onClick={() => engine.retry(orc.id)}>
            Retry
          </button>
          <button onClick={() => engine.abandon(orc.id)}>Abandon</button>
        </div>
      )}

      <h3>Log</h3>
      <div className="log" ref={logRef}>
        {orc.log.map((l, i) => (
          <div key={i} className={`l-${l.level}`}>
            {l.text}
          </div>
        ))}
      </div>
    </div>
  );
}

/** The repo the chieftain's peons work on (cloned into black boxes, never edited in place). */
function WarTarget() {
  const repo = useBoard((s) => s.repo);
  const [draft, setDraft] = useState(repo ?? "");
  useEffect(() => setDraft(repo ?? ""), [repo]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (draft.trim()) engine.setRepo(draft.trim());
  };
  return (
    <form className="target" onSubmit={submit}>
      <h3>War target</h3>
      <div className="row">
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="/path/to/repo or https://...git" />
        <button type="submit" disabled={!draft.trim() || draft.trim() === repo}>
          Set
        </button>
      </div>
      <p className="hint">
        Peons work on copies in black boxes. Finished work lands here as <code>orc/...</code> branches for you to
        merge.
      </p>
    </form>
  );
}

function WarLog() {
  const feed = useBoard((s) => s.feed);
  return (
    <div className="warlog panel">
      <h3>War log</h3>
      {feed.slice(-6).map((l, i) => (
        <div key={`${l.t}-${i}`} className={`l-${l.level}`}>
          <b>{l.name}:</b> {l.text}
        </div>
      ))}
    </div>
  );
}

function CommandBar() {
  const selected = useBoard((s) => s.selected);
  const orcs = useBoard((s) => s.orcs);
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const names = selected.map((id) => orcs[id]?.name).filter(Boolean);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      const typing = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement;
      if (e.key === "Enter" && !typing) {
        e.preventDefault();
        input.current?.focus();
      } else if (e.key === "Escape" && el === input.current) {
        input.current?.blur();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const order = text.trim();
    if (!order || !selected.length) return;
    engine.command(selected, order);
    setText("");
  };

  return (
    <form className="command panel" onSubmit={submit}>
      <div className="to">{names.length ? <>Orders for <b>{names.join(", ")}</b></> : "Select peons to give orders"}</div>
      <div className="row">
        <input
          ref={input}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={names.length ? 'e.g. "Add a login page"' : "No peon selected"}
          disabled={!names.length}
        />
        <button type="submit" className="ok" disabled={!names.length || !text.trim()}>
          Work work!
        </button>
      </div>
    </form>
  );
}
