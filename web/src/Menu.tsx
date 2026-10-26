// The in-game menu: resume, settings (agent credentials), controls, quit.
// Opened with Esc (when nothing is selected), the Menu button, or by closing the window.

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { engine } from "./engines";
import { board, openMenu, useBoard, type MenuView } from "./store";
import type { CredentialName, CredentialStatus } from "./types";

interface CredentialInfo {
  name: CredentialName;
  label: string;
  runtime: string;
  help: ReactNode;
  placeholder: string;
}

const CREDENTIALS: CredentialInfo[] = [
  {
    name: "CLAUDE_CODE_OAUTH_TOKEN",
    label: "Claude subscription token",
    runtime: "claude peons",
    placeholder: "sk-ant-oat01-...",
    help: (
      <>
        Uses your Claude Pro or Max plan. In a terminal run <code>claude setup-token</code>, sign in, and paste the
        token it prints.
      </>
    ),
  },
  {
    name: "CURSOR_API_KEY",
    label: "Cursor API key",
    runtime: "cursor peons",
    placeholder: "key_...",
    help: (
      <>
        Create a user API key in{" "}
        <ExternalLink href="https://cursor.com/dashboard/api">Cursor Dashboard → API Keys</ExternalLink> and paste it.
      </>
    ),
  },
  {
    name: "ANTHROPIC_API_KEY",
    label: "Anthropic API key (optional)",
    runtime: "claude peons",
    placeholder: "sk-ant-api03-...",
    help: <>Pay-per-use API billing instead of a subscription. Only needed if you don't use the token above.</>,
  },
];

const STATUS_LABEL: Record<CredentialStatus, string> = {
  saved: "Saved",
  env: "From environment",
  missing: "Not set",
};

export function hasAnyCredential(status: Record<CredentialName, CredentialStatus> | null): boolean {
  return status !== null && Object.values(status).some((s) => s !== "missing");
}

export function Menu() {
  const view = useBoard((s) => s.menu);
  useMenuKeys(view);
  useFirstRunSettings();
  if (!view) return null;
  return (
    <div className="menu-backdrop" onMouseDown={(e) => e.target === e.currentTarget && openMenu(null)}>
      <div className="menu panel" role="dialog" aria-modal="true" aria-label="Menu">
        {view === "main" && <MainView />}
        {view === "settings" && <SettingsView />}
        {view === "controls" && <ControlsView />}
        {view === "quit" && <QuitView />}
      </div>
    </div>
  );
}

function MainView() {
  return (
    <>
      <h1 className="menu-title">ORC</h1>
      <p className="menu-sub">War council</p>
      <div className="menu-buttons">
        <button className="ok" autoFocus onClick={() => openMenu(null)}>
          Resume
        </button>
        <button onClick={() => openMenu("settings")}>Settings</button>
        <button onClick={() => openMenu("controls")}>Controls</button>
        <button className="danger" onClick={() => openMenu("quit")}>
          Quit
        </button>
      </div>
    </>
  );
}

function SettingsView() {
  const link = useBoard((s) => s.link);
  const status = useBoard((s) => s.credentials);
  const error = useBoard((s) => s.credentialError);
  return (
    <>
      <Header title="Settings" />
      <h3>Agent credentials</h3>
      {link === "sim" ? (
        <p className="hint">The simulation runs no real agents, so it needs no credentials.</p>
      ) : link !== "live" || !status ? (
        <p className="hint">Waiting for the engine...</p>
      ) : (
        <>
          <p className="hint">
            Peons need these to work inside their black boxes. They are saved to <code>~/.config/orc/env</code>,
            readable only by you, and take effect immediately. Saved values are never shown again.
          </p>
          {error && <p className="menu-error">{error}</p>}
          {CREDENTIALS.map((c) => (
            <CredentialRow key={c.name} info={c} status={status[c.name]} />
          ))}
        </>
      )}
    </>
  );
}

function CredentialRow({ info, status }: { info: CredentialInfo; status: CredentialStatus }) {
  const [value, setValue] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;
    engine.setCredential(info.name, value.trim());
    setValue(""); // don't keep the secret in the page any longer than needed
  };
  return (
    <form className="cred" onSubmit={submit}>
      <div className="cred-head">
        <b>{info.label}</b>
        <span className={`cred-status c-${status}`}>{STATUS_LABEL[status]}</span>
      </div>
      <p className="hint">
        {info.help} <span className="muted">For {info.runtime}.</span>
      </p>
      {status === "env" ? (
        <p className="hint">Set in your shell environment, which takes priority. Change it there.</p>
      ) : (
        <div className="row">
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={status === "saved" ? "Paste a new value to replace it" : info.placeholder}
          />
          <button type="submit" className="ok" disabled={!value.trim()}>
            Save
          </button>
          {status === "saved" && (
            <button type="button" onClick={() => engine.clearCredential(info.name)}>
              Remove
            </button>
          )}
        </div>
      )}
    </form>
  );
}

function ControlsView() {
  const rows: [string, string][] = [
    ["Select a peon", "Left-click the peon or its hut"],
    ["Select several", "Drag a box; shift-click to add or remove"],
    ["Give orders", "Enter, type, Enter"],
    ["Pan", "Right-drag, or WASD / arrow keys"],
    ["Zoom", "Mouse wheel"],
    ["Rotate", "Middle-drag, or Q / E"],
    ["Recentre", "H"],
    ["Deselect / menu", "Esc"],
    ["Fullscreen", "F11"],
    ["Quit", "Ctrl+Q"],
  ];
  return (
    <>
      <Header title="Controls" />
      <table className="controls">
        <tbody>
          {rows.map(([action, keys]) => (
            <tr key={action}>
              <td>{action}</td>
              <td>{keys}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function QuitView() {
  const orcs = useBoard((s) => s.orcs);
  const link = useBoard((s) => s.link);
  const [leaving, setLeaving] = useState(false);
  const busy = Object.values(orcs).filter((o) => o.state !== "idle" && o.state !== "failed").length;
  const desktop = window.orcApp;
  const quit = () => {
    setLeaving(true);
    void desktop?.quit();
  };
  return (
    <>
      <h2>Leave the village?</h2>
      {link !== "sim" && busy > 0 ? (
        <p className="menu-error">
          {busy} {busy === 1 ? "peon is" : "peons are"} still working. Quitting stops them and destroys their black
          boxes; unfinished work is lost. Branches already delivered stay in your repo.
        </p>
      ) : (
        <p className="hint">Nothing is running. Delivered branches stay in your repo.</p>
      )}
      {desktop ? (
        <div className="menu-buttons">
          <button className="danger" autoFocus onClick={quit} disabled={leaving}>
            {leaving ? "Stopping the engine..." : "Quit"}
          </button>
          <button onClick={() => openMenu(null)} disabled={leaving}>
            Cancel
          </button>
        </div>
      ) : (
        <p className="hint">Running in a browser: close the tab to leave.</p>
      )}
    </>
  );
}

function Header({ title }: { title: string }) {
  return (
    <div className="menu-head">
      <button className="back" onClick={() => openMenu("main")} aria-label="Back">
        ←
      </button>
      <h2>{title}</h2>
    </div>
  );
}

function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      onClick={(e) => {
        e.preventDefault();
        void window.orcApp?.openExternal(href);
      }}
    >
      {children}
    </a>
  );
}

/** Esc opens the menu when nothing is selected, and closes or backs out of it. */
function useMenuKeys(view: MenuView | null) {
  const viewRef = useRef(view);
  viewRef.current = view;
  useEffect(() => {
    // Capture phase on window runs before the map's own key handling.
    const onKey = (e: KeyboardEvent) => {
      const current = viewRef.current;
      if (current) {
        if (e.key === "Escape") openMenu(current === "main" ? null : "main");
        e.stopPropagation(); // the map and command bar ignore keys while the menu is open
        return;
      }
      const el = document.activeElement;
      const typing = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement;
      if (e.key === "Escape" && !typing && board.getState().selected.length === 0) {
        e.stopPropagation();
        openMenu("main");
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
}

/** First launch with no credentials at all: open Settings once so new players see where to paste a token. */
function useFirstRunSettings() {
  const link = useBoard((s) => s.link);
  const status = useBoard((s) => s.credentials);
  const shown = useRef(false);
  useEffect(() => {
    if (shown.current || link !== "live" || status === null) return;
    shown.current = true;
    if (!hasAnyCredential(status) && !board.getState().menu) openMenu("settings");
  }, [link, status]);
}
