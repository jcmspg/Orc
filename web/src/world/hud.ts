// DOM helpers shared by the world: where the HUD covers the screen, and
// whether the player is typing (so keys don't move the camera).

export interface Insets {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** Screen space covered by HUD panels, measured from the DOM. */
export function hudInsets(W: number, H: number): Insets {
  const inset = { left: 0, right: 0, top: 0, bottom: 0 };
  // Hidden panels (display: none) measure as all zeros: skip them.
  const rect = (sel: string) => {
    const r = document.querySelector(sel)?.getBoundingClientRect();
    return r && r.width > 0 ? r : undefined;
  };
  const top = rect(".topbar");
  if (top) inset.top = top.bottom + 8;
  // On narrow windows the barracks is a full-width row under the top bar.
  const barracks = rect(".barracks");
  if (barracks && barracks.width > W * 0.4) inset.top = Math.max(inset.top, barracks.bottom + 8);
  const command = rect(".command");
  if (command) inset.bottom = H - command.top + 8;
  const warlog = rect(".warlog");
  if (warlog) inset.bottom = Math.max(inset.bottom, H - warlog.top + 8);
  const unit = rect(".unit");
  if (unit) {
    // Side panel on wide windows, bottom sheet on narrow ones.
    if (unit.left > W / 2) inset.right = W - unit.left + 8;
    else inset.bottom = Math.max(inset.bottom, H - unit.top + 8);
  }
  return inset;
}

export function isTyping(): boolean {
  const el = document.activeElement;
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement;
}
