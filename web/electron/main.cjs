// Desktop shell: one native window running the game, plus the engine server.
// Dev:  ORC_DEV_URL=http://localhost:5173 electron .   (hot reload from Vite)
// Prod: electron .                                     (loads the built dist/)
// ORC_SIM=1 runs the in-app simulation instead of the real engine.
// ORC_NO_SERVER=1 skips starting `orc serve` (e.g. when you run it yourself).
// ORC_PORT picks the engine port (default 47820).

const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { app, BrowserWindow, Menu } = require("electron");

// Native Wayland when available (Hyprland), X11 otherwise.
app.commandLine.appendSwitch("ozone-platform-hint", "auto");

const SIM = Boolean(process.env.ORC_SIM);
const PORT = process.env.ORC_PORT || "47820";
const QUERY = SIM ? "sim" : `server=${encodeURIComponent(`ws://127.0.0.1:${PORT}/ws`)}`;
let server = null;

/** Start the Python engine from the repo's venv. If one is already listening, ours just exits. */
function startServer() {
  if (SIM || process.env.ORC_NO_SERVER) return;
  const bin = path.join(__dirname, "..", "..", ".venv", "bin", "orc");
  if (!fs.existsSync(bin)) {
    console.warn(`orc: ${bin} not found; start the engine yourself with \`orc serve\`.`);
    return;
  }
  server = spawn(bin, ["serve", "--port", PORT], { stdio: "inherit", env: process.env });
  server.on("exit", () => {
    server = null;
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 640,
    minHeight: 420,
    resizable: true,
    title: "orc",
    backgroundColor: "#2a3b1c",
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  const devUrl = process.env.ORC_DEV_URL;
  if (devUrl) win.loadURL(`${devUrl}?${QUERY}`);
  else win.loadFile(path.join(__dirname, "..", "dist", "index.html"), { search: QUERY });

  // Game-style controls: F11 fullscreen, F12 devtools.
  win.webContents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown") return;
    if (input.key === "F11") {
      win.setFullScreen(!win.isFullScreen());
      event.preventDefault();
    } else if (input.key === "F12") {
      win.webContents.toggleDevTools();
      event.preventDefault();
    }
  });

  // Never navigate away from the game or open popups.
  win.webContents.on("will-navigate", (event, url) => {
    if (url !== win.webContents.getURL()) event.preventDefault();
  });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  startServer();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// The server destroys its black boxes on shutdown, so ask it to stop politely.
app.on("will-quit", () => {
  server?.kill("SIGTERM");
});
