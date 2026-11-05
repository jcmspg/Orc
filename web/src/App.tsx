import { useEffect, useRef, useState, type FormEvent } from "react";
import { engine } from "./engines";
import { GameView } from "./game/GameView";
import { Menu, hasAnyCredential } from "./Menu";
import { DiffViewer, SpoilsPanel } from "./Spoils";
import { openMenu, select, toggleSpoils, useBoard, type Link } from "./store";
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
      <SpoilsPanel />
      <DiffViewer />
      <Menu />
    </div>
  );
}

function TopBar() {
  const orcs = useBoard((s) => s.orcs);
  const gold = useBoard((s) => s.gold);
  const link = useBoard((s) => s.link);
  const deliveries = useBoard((s) => s.deliveries);
  const spoilsOpen = useBoard((s) => s.spoils);
  const list = Object.values(orcs);
  const busy = list.filter((o) => o.state !== "idle").length;
  const waiting = list.filter((o) => o.state === "awaiting").length;
  const failed = list.filter((o) => o.state === "failed").length;
  const unmerged = deliveries.filter((d) => d.status === "delivered" || d.status === "conflict").length;
  return (
    <div className="topbar panel">
      <button className="menu-button" onClick={() => openMenu("main")} title="Menu (Esc)">
        Menu
      </button>
      <span className="brand">ORC</span>
      <button
        className={`gold-button${spoilsOpen ? " on" : ""}`}
        onClick={() => toggleSpoils()}
        title="Spoils: delivered branches (G)"
      >
        <span className="gold">{gold} gold</span>
        {unmerged > 0 && <span className="to-merge">{unmerged} to merge</span>}
      </button>
      <span>{list.length} peons</span>
      <span>{busy} busy</span>
      {waiting > 0 && <span className="warn">{waiting} awaiting orders</span>}
      {failed > 0 && <span className="bad">{failed} failed</span>}
      <span className={`badge link-${link}`}>{LINK_LABEL[link]}</span>
    </div>
  );
}

/** Runtimes the real engine can run inside black boxes (the simulation fakes them all). */
const BOX_READY: Record<Backend, boolean> = { claude: true, cursor: true, copilot: true, codex: true };

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
  const credentials = useBoard((s) => s.credentials);
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
        {link === "live" && credentials && !hasAnyCredential(credentials) && (
          <div className="pending">
            Peons need a Claude token or Cursor key to work.{" "}
            <button className="ok" onClick={() => openMenu("settings")}>
              Add one
            </button>
          </div>
        )}
        {!repo && (
          <p className="pending">
            No war target yet. Select <b>Ozgar</b> and set the repo the peons should work on.
          </p>
        )}
        <h2>No peon selected</h2>
        <p className="hint">
          Click a peon or drag a box around several. Shift-click adds to the selection. Right-drag or WASD pans,
          scroll zooms, middle-drag or Q/E rotates, H recentres, Enter gives orders, Esc deselects (or opens the
          menu). F11 fullscreen.
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
      <div className="unit-body">
      {orc.task && <p className="task">{orc.task}</p>}
      {orc.role === "chieftain" && (
        <>
          <WarTarget />
          <RemoteTarget />
        </>
      )}

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
    </div>
  );
}

/** The repo the chieftain's peons work on (cloned into black boxes, never edited in place). */
function WarTarget() {
  const repo = useBoard((s) => s.repo);
  const remote = useBoard((s) => s.remote);
  const local = remote ? null : repo; // an ssh target is shown (and changed) in its own form
  const [draft, setDraft] = useState(local ?? "");
  useEffect(() => setDraft(local ?? ""), [local]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (draft.trim()) engine.setRepo(draft.trim());
  };
  return (
    <form className="target" onSubmit={submit}>
      <h3>War target</h3>
      <div className="row">
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="/path/to/repo or https://...git" />
        <button type="submit" disabled={!draft.trim() || draft.trim() === local}>
          Set
        </button>
      </div>
      <p className="hint">
        Peons work on copies in black boxes. Finished work lands here as <code>orc/...</code> branches; click the
        gold to look them over and merge.
      </p>
    </form>
  );
}

const SSH_KEY_HINTS = ["id_ed25519", "id_ecdsa", "id_rsa"];
const SSH_KEY_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
/** How long to wait for the engine's answer before the form frees up (ConnectTimeout is 10 s). */
const REMOTE_TIMEOUT_MS = 20000;

/**
 * A war target on another machine, reached over ssh: `user@host:path`, an optional port and the
 * *name* of a key in ~/.ssh. The engine does the real validation and ssh; only names travel here.
 */
function RemoteTarget() {
  const remote = useBoard((s) => s.remote);
  const [target, setTarget] = useState(remote?.target ?? "");
  const [port, setPort] = useState(remote?.port ? String(remote.port) : "");
  const [key, setKey] = useState(remote?.key ?? "");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    // Any new remote state (set, or cleared by a local repo) is the answer to the request.
    setBusy(false);
    setTarget(remote?.target ?? "");
    setPort(remote?.port ? String(remote.port) : "");
    setKey(remote?.key ?? "");
  }, [remote]);
  useEffect(() => {
    if (!busy) return;
    const t = window.setTimeout(() => setBusy(false), REMOTE_TIMEOUT_MS);
    return () => window.clearTimeout(t);
  }, [busy]);

  const portNum = port.trim() === "" ? null : Number(port);
  const problem =
    target.trim() === ""
      ? null
      : !/^\S+:\S+$/.test(target.trim())
        ? "Use user@host:path/to/repo."
        : portNum !== null && !(Number.isInteger(portNum) && portNum > 0 && portNum < 65536)
          ? "Port must be 1-65535."
          : key.trim() !== "" && !SSH_KEY_NAME.test(key.trim())
            ? "Key is a file name in ~/.ssh, not a path."
            : null;
  const same = remote !== null && remote.target === target.trim() && remote.port === portNum && (remote.key ?? "") === key.trim();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!target.trim() || problem || busy) return;
    setBusy(true);
    engine.setRemote(target.trim(), portNum, key.trim() || null);
  };
  return (
    <form className="target remote" onSubmit={submit}>
      <h3>
        SSH war target{" "}
        {remote ? <span className="pill rs-on">ssh</span> : <span className="pill">off</span>}
      </h3>
      <div className="row">
        <input
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="user@host:path/to/repo"
          aria-label="SSH target"
          spellCheck={false}
        />
      </div>
      <div className="row">
        <input
          className="port"
          value={port}
          onChange={(e) => setPort(e.target.value)}
          inputMode="numeric"
          placeholder="port"
          aria-label="SSH port"
        />
        <input
          value={key}
          onChange={(e) => setKey(e.target.value)}
          list="ssh-key-names"
          placeholder="key name in ~/.ssh"
          aria-label="SSH key name"
          spellCheck={false}
        />
        <datalist id="ssh-key-names">
          {SSH_KEY_HINTS.map((k) => (
            <option key={k} value={k} />
          ))}
        </datalist>
        <button type="submit" disabled={!target.trim() || problem !== null || busy || same}>
          {busy ? "Connecting..." : "Connect"}
        </button>
      </div>
      {problem && <p className="hint bad-text">{problem}</p>}
      <p className="hint">
        Code is bundled over ssh into black boxes, never mounted. Finished branches come back as{" "}
        <code>orc/...</code> and merges use the same safe rules there; if the branch is checked out on that machine
        you get an <code>ssh</code> command to run. Only the key's <i>name</i> is sent. The host must already be in
        your <code>known_hosts</code> (connect once from a terminal). Setting a local path above switches back.
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
