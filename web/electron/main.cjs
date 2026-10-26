// Desktop shell: one native window running the game.
// Dev:  ORC_DEV_URL=http://localhost:5173 electron .   (hot reload from Vite)
// Prod: electron .                                     (loads the built dist/)

const path = require("node:path");
const { app, BrowserWindow, Menu } = require("electron");

// Native Wayland when available (Hyprland), X11 otherwise.
app.commandLine.appendSwitch("ozone-platform-hint", "auto");

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
  if (devUrl) win.loadURL(devUrl);
  else win.loadFile(path.join(__dirname, "..", "dist", "index.html"));

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
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
