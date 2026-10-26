import { useEffect, useRef, useState, type FormEvent } from "react";
import { engine } from "./engine";
import { GameView } from "./game/GameView";
import { select, useBoard } from "./store";
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
      <span className="badge">simulated</span>
    </div>
  );
}

function Barracks() {
  const [backend, setBackend] = useState<Backend>("claude");
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
            <option key={b}>{b}</option>
          ))}
        </select>
      </label>
    </div>
  );
}

function UnitPanel() {
  const selected = useBoard((s) => s.selected);
  const orcs = useBoard((s) => s.orcs);
  const units = selected.map((id) => orcs[id]).filter(Boolean);

  if (!units.length) {
    return (
      <div className="unit panel compact">
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
