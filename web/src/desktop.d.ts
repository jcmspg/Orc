// Bridge exposed by web/electron/preload.cjs. Absent when the page runs in a plain browser.
interface OrcDesktop {
  quit(): Promise<void>;
  openExternal(url: string): Promise<boolean>;
  onQuitRequest(callback: () => void): void;
}

interface Window {
  orcApp?: OrcDesktop;
}
