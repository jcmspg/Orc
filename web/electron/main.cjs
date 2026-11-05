// Desktop shell: one native window running the game, plus the engine server.
// Dev:  ORC_DEV_URL=http://localhost:5173 electron .   (hot reload from Vite)
// Prod: electron .                                     (loads the built dist/)
// ORC_SIM=1 runs the in-app simulation instead of the real engine.
// ORC_NO_SERVER=1 skips starting `orc serve` (e.g. when you run it yourself).
// ORC_PORT picks the engine port (default 47820).
// Packaged builds start the frozen engine from <resources>/engine/ instead of the dev venv.

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { app, BrowserWindow, Menu, ipcMain, shell } = require("electron");

// Native Wayland when available (Hyprland), X11 otherwise.
app.commandLine.appendSwitch("ozone-platform-hint", "auto");

const SIM = Boolean(process.env.ORC_SIM);
const OWN_SERVER = !SIM && !process.env.ORC_NO_SERVER;
const PORT = process.env.ORC_PORT || "47820";
// Per-launch secret shared only with our engine and our window, so web pages
// (which can open WebSockets to localhost) can't drive the engine.
const SESSION = OWN_SERVER ? crypto.randomBytes(24).toString("hex") : "";
const QUERY = SIM
  ? "sim"
  : `server=${encodeURIComponent(`ws://127.0.0.1:${PORT}/ws${SESSION ? `?token=${SESSION}` : ""}`)}`;
const EXTERNAL_HOSTS = new Set(["cursor.com", "www.cursor.com", "claude.com", "docs.claude.com", "code.claude.com", "github.com", "platform.openai.com"]);
const ENGINE_STOP_TIMEOUT_MS = 10_000;

let server = null;
let quitting = false; // the user confirmed (or the app is being shut down from outside)
let pageConfirmsQuit = false; // the page registered a quit-confirmation handler

const WIN = process.platform === "win32";

/** The frozen engine shipped in the installer (extraResources), or the repo's venv `orc` in dev. */
function enginePath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "engine", WIN ? "orc-engine.exe" : "orc-engine");
  }
  const venv = path.join(__dirname, "..", "..", ".venv");
  return WIN ? path.join(venv, "Scripts", "orc.exe") : path.join(venv, "bin", "orc");
}

/** Start the Python engine. If one is already listening, ours just exits. */
function startServer() {
  if (!OWN_SERVER) return;
  const bin = enginePath();
  if (!fs.existsSync(bin)) {
    console.warn(`orc: ${bin} not found; start the engine yourself with \`orc serve\`.`);
    return;
  }
  server = spawn(bin, ["serve", "--port", PORT], {
    // We keep the engine's stdin open; closing it is the portable "stop cleanly" request.
    stdio: ["pipe", "inherit", "inherit"],
    windowsHide: true,
    env: {
      ...process.env,
      ORC_SESSION_TOKEN: SESSION,
      // The engine also stops itself if this process disappears without stopping it.
      ORC_PARENT_PID: String(process.pid),
      ORC_STOP_ON_STDIN_EOF: "1",
    },
  });
  server.stdin.on("error", () => {}); // EPIPE if it already exited
  server.on("error", (err) => {
    console.warn(`orc: couldn't start the engine (${err.message}).`);
    server = null;
  });
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
      preload: path.join(__dirname, "preload.cjs"),
    },
  });

  const devUrl = process.env.ORC_DEV_URL;
  if (devUrl) win.loadURL(`${devUrl}?${QUERY}`);
  else win.loadFile(path.join(__dirname, "..", "dist", "index.html"), { search: QUERY });

  // Closing the window asks the page first (it shows a confirmation with what's still running).
  win.on("close", (event) => {
    if (quitting || !pageConfirmsQuit) return;
    event.preventDefault();
    win.webContents.send("orc:quit-request");
  });
  win.webContents.on("render-process-gone", () => {
    pageConfirmsQuit = false; // a crashed page can't confirm; let the window close normally
  });

  // Game-style controls: F11 fullscreen, F12 devtools, Ctrl+Q quit.
  win.webContents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown") return;
    if (input.key === "F11") {
      win.setFullScreen(!win.isFullScreen());
      event.preventDefault();
    } else if (input.key === "F12") {
      win.webContents.toggleDevTools();
      event.preventDefault();
    } else if (input.control && input.key.toLowerCase() === "q") {
      event.preventDefault();
      if (pageConfirmsQuit) win.webContents.send("orc:quit-request");
      else app.quit();
    }
  });

  // Never navigate away from the game or open popups.
  win.webContents.on("will-navigate", (event, url) => {
    if (url !== win.webContents.getURL()) event.preventDefault();
  });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
}

ipcMain.on("orc:quit-handler-ready", () => {
  pageConfirmsQuit = true;
});

ipcMain.handle("orc:quit", () => {
  quitting = true;
  app.quit();
});

ipcMain.handle("orc:open-external", (_event, url) => {
  let parsed;
  try {
    parsed = new URL(String(url));
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:" || !EXTERNAL_HOSTS.has(parsed.hostname)) return false;
  shell.openExternal(parsed.toString());
  return true;
});

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  startServer();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", () => {
  quitting = true;
});

// Being killed from outside (kill, logout, Ctrl+C in the terminal) takes the clean path too.
for (const sig of ["SIGTERM", "SIGINT", "SIGHUP"]) {
  process.on(sig, () => {
    quitting = true;
    app.quit();
  });
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// Let the engine stop cleanly (it destroys its black boxes) before the app exits.
app.on("will-quit", (event) => {
  if (!server) return;
  event.preventDefault();
  const proc = server;
  const force = setTimeout(() => {
    proc.kill(WIN ? undefined : "SIGKILL");
    app.exit(0);
  }, ENGINE_STOP_TIMEOUT_MS);
  proc.once("exit", () => {
    clearTimeout(force);
    app.exit(0);
  });
  // Windows has no gentle SIGTERM (kill() would terminate hard), so ask by closing stdin there.
  if (WIN) proc.stdin.end();
  else proc.kill("SIGTERM");
});
