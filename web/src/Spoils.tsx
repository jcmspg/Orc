// The spoils: branches the peons delivered. Lists them, shows their diffs, and
// merges them when the engine can do it without touching a working tree
// (otherwise it shows the command to run).

import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { engine } from "./engines";
import { board, toggleSpoils, useBoard, viewDiff } from "./store";
import type { Delivery, DeliveryStatus } from "./types";

const STATUS_LABEL: Record<DeliveryStatus, string> = {
  delivered: "Ready to merge",
  bundled: "Bundled",
  merged: "Merged",
  conflict: "Conflict",
  missing: "Missing",
  moved: "Moved",
};

/** How long to wait for the engine's answer to a merge before giving up on the spinner. */
const MERGE_TIMEOUT_MS = 15000;

const canMerge = (d: Delivery) => d.status === "delivered" || d.status === "conflict";

export function SpoilsPanel() {
  const open = useBoard((s) => s.spoils);
  const deliveries = useBoard((s) => s.deliveries);
  useSpoilsKey();
  if (!open) return null;
  const newest = [...deliveries].reverse();
  return (
    <div className="spoils panel" role="region" aria-label="Spoils">
      <div className="spoils-head">
        <h2>Spoils</h2>
        <button className="back" onClick={() => toggleSpoils(false)} title="Close (G)">
          ×
        </button>
      </div>
      {newest.length === 0 ? (
        <p className="hint">
          No gold yet. Every reviewed branch the peons bring home shows up here, to look over and merge.
        </p>
      ) : (
        <ul className="spoils-list">
          {newest.map((d) => (
            <SpoilsItem key={d.id} delivery={d} />
          ))}
        </ul>
      )}
    </div>
  );
}

function SpoilsItem({ delivery: d }: { delivery: Delivery }) {
  const owner = useBoard((s) => s.orcs[d.owner]);
  const [confirming, setConfirming] = useState(false);
  const [merging, setMerging] = useState(false);
  const asked = useRef<Delivery | null>(null);

  // Any update to this delivery after a merge request is the engine's answer.
  useEffect(() => {
    if (merging && asked.current !== d) setMerging(false);
  }, [d, merging]);
  useEffect(() => {
    if (!merging) return;
    const t = window.setTimeout(() => setMerging(false), MERGE_TIMEOUT_MS);
    return () => window.clearTimeout(t);
  }, [merging]);

  const merge = () => {
    asked.current = d;
    setConfirming(false);
    setMerging(true);
    engine.merge(d.id);
  };
  const into = d.baseBranch;

  return (
    <li className={`spoil st-${d.status}`}>
      <div className="spoil-top">
        <b className="spoil-task">{d.task}</b>
        <span className={`pill st-${d.status}`}>{STATUS_LABEL[d.status]}</span>
      </div>
      <code className="spoil-branch" title={d.branch}>
        {d.branch}
      </code>
      <div className="spoil-meta muted">
        {owner ? <span className={`r-${owner.role}`}>{owner.name}</span> : <span>a lost peon</span>}
        <span>{d.stat || "no stat"}</span>
        <span>{into ? <>from {into}</> : "detached HEAD"}</span>
      </div>
      {d.status === "merged" && d.mergeCommit && (
        <p className="spoil-note ok-text">
          Merged into {into} as <code>{d.mergeCommit.slice(0, 8)}</code>.
        </p>
      )}
      {d.note && <p className="spoil-note">{d.note}</p>}
      {d.conflicts.length > 0 && (
        <ul className="conflicts">
          {d.conflicts.map((f) => (
            <li key={f}>
              <code>{f}</code>
            </li>
          ))}
        </ul>
      )}
      {d.command && <CopyCommand command={d.command} />}

      {confirming ? (
        <div className="pending confirm">
          <p>
            Merge <code>{d.branch}</code> into <b>{into ?? "?"}</b>?
          </p>
          <p className="hint">
            orc writes the merge commit straight onto {into ?? "the branch"}, never into a working tree. If{" "}
            {into ?? "it"} is checked out somewhere, nothing moves and you get the command instead.
          </p>
          <div className="row">
            <button className="ok" autoFocus onClick={merge}>
              Merge
            </button>
            <button onClick={() => setConfirming(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="row">
          <button onClick={() => viewDiff(d.id)}>View diff</button>
          {canMerge(d) && (
            <button className="ok" disabled={merging} onClick={() => setConfirming(true)}>
              {merging ? "Merging..." : d.status === "conflict" ? "Try merge again" : "Merge"}
            </button>
          )}
        </div>
      )}
    </li>
  );
}

function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const ref = useRef<HTMLElement>(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // No clipboard access: select the text so Ctrl+C works.
      const el = ref.current;
      const sel = window.getSelection();
      if (el && sel) {
        sel.selectAllChildren(el);
      }
    }
  };
  return (
    <div className="copy-command">
      <code ref={ref}>{command}</code>
      <button onClick={copy} title="Copy to clipboard">
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

/** G toggles the spoils (when not typing and no menu is open). */
function useSpoilsKey() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      const typing = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement;
      if (typing || e.ctrlKey || e.metaKey || e.altKey || board.getState().menu) return;
      if (e.code === "KeyG") toggleSpoils();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

// --- diff viewer ---

interface DiffFile {
  path: string;
  added: number;
  removed: number;
  lines: string[];
}

/** Split a unified diff into files, counting added and removed lines. */
function parseDiff(text: string): DiffFile[] {
  const files: DiffFile[] = [];
  let cur: DiffFile | null = null;
  let inHunk = false; // "--- " and "+++ " are file headers only before the first hunk; after it they're changed lines
  for (const line of text.split("\n")) {
    if (line.startsWith("diff --git ")) {
      const m = / b\/(.*)$/.exec(line);
      cur = { path: m ? m[1] : line.slice(11), added: 0, removed: 0, lines: [] };
      files.push(cur);
      inHunk = false;
      continue;
    }
    if (!cur) continue;
    if (line.startsWith("@@")) inHunk = true;
    if (!inHunk && (line.startsWith("+++ ") || line.startsWith("--- "))) {
      if (line.startsWith("+++ b/")) cur.path = line.slice(6);
      continue;
    }
    if (line.startsWith("+")) cur.added++;
    else if (line.startsWith("-")) cur.removed++;
    cur.lines.push(line);
  }
  if (files.length && files[files.length - 1].lines.at(-1) === "") files[files.length - 1].lines.pop();
  return files;
}

function lineClass(line: string): string {
  if (line.startsWith("@@")) return "d-hunk";
  if (line.startsWith("+")) return "d-add";
  if (line.startsWith("-")) return "d-del";
  if (line.startsWith("\\")) return "d-meta";
  if (/^(new|deleted) file mode|^index |^similarity|^rename |^old mode|^new mode|^Binary files/.test(line)) return "d-meta";
  return "d-ctx";
}

export function DiffViewer() {
  const id = useBoard((s) => s.viewing);
  const delivery = useBoard((s) => (s.viewing ? s.deliveries.find((d) => d.id === s.viewing) : undefined));
  const reply = useBoard((s) => (s.viewing ? s.diffs[s.viewing] : undefined));
  const files = useMemo(() => (reply ? parseDiff(reply.text) : []), [reply]);

  // Ask for a fresh diff each time the viewer opens; drop the old one so "loading" is honest.
  useEffect(() => {
    if (!id) return;
    board.setState((s) => {
      const { [id]: _old, ...rest } = s.diffs;
      return { diffs: rest };
    });
    engine.diff(id);
  }, [id]);

  useEffect(() => {
    if (!id) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || board.getState().menu) return;
      e.stopImmediatePropagation(); // close the viewer, don't open the menu or deselect
      viewDiff(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [id]);

  if (!id || !delivery) return null;
  return (
    <div className="diff-backdrop" onMouseDown={(e) => e.target === e.currentTarget && viewDiff(null)}>
      <div className="diff panel" role="dialog" aria-modal="true" aria-label={`Diff of ${delivery.branch}`}>
        <div className="diff-head">
          <div className="diff-title">
            <h2>{delivery.task}</h2>
            <code className="muted">
              {delivery.baseBranch ?? delivery.base.slice(0, 8)} ← {delivery.branch}
            </code>
          </div>
          <span className={`pill st-${delivery.status}`}>{STATUS_LABEL[delivery.status]}</span>
          <button className="back" onClick={() => viewDiff(null)} title="Close (Esc)">
            ×
          </button>
        </div>
        <div className="diff-stat muted">
          {delivery.stat}
          {files.length > 0 && (
            <span className="diff-files">
              {files.map((f) => (
                <a key={f.path} href={`#diff-${f.path}`} onClick={(e) => jump(e, f.path)}>
                  {f.path}
                </a>
              ))}
            </span>
          )}
        </div>
        <div className="diff-body">
          {!reply && <p className="hint">Unrolling the scroll...</p>}
          {reply?.error && <p className="spoil-note bad">{reply.error}</p>}
          {reply && !reply.error && files.length === 0 && <p className="hint">No changes against the base.</p>}
          {files.map((f) => (
            <section key={f.path} className="diff-file" id={`diff-${f.path}`}>
              <header>
                <code>{f.path}</code>
                <span className="d-add-n">+{f.added}</span>
                <span className="d-del-n">−{f.removed}</span>
              </header>
              <pre>
                {f.lines.map((line, i) => (
                  <div key={i} className={lineClass(line)}>
                    {line || " "}
                  </div>
                ))}
              </pre>
            </section>
          ))}
          {reply?.truncated && (
            <p className="spoil-note warn">The diff is too big to show in full; it was cut here. See the rest with git.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function jump(e: MouseEvent, path: string) {
  e.preventDefault();
  document.getElementById(`diff-${path}`)?.scrollIntoView({ block: "start" });
}
