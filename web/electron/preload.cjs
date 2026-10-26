// The only bridge between the game window and the desktop shell. The page gets
// exactly these functions; no Node or Electron APIs are exposed.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("orcApp", {
  /** Shut the engine down cleanly (destroying black boxes), then exit. */
  quit: () => ipcRenderer.invoke("orc:quit"),
  /** Open a link in the system browser (allow-listed hosts only). */
  openExternal: (url) => ipcRenderer.invoke("orc:open-external", url),
  /** Called when the user closes the window or presses Ctrl+Q, so the page can confirm. */
  onQuitRequest: (callback) => {
    ipcRenderer.on("orc:quit-request", () => callback());
    ipcRenderer.send("orc:quit-handler-ready");
  },
});
