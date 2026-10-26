import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Relative asset paths so Electron can load dist/index.html from disk.
  base: "./",
  // Phaser alone is ~1.5 MB; size warnings don't matter for a local desktop app.
  build: { chunkSizeWarningLimit: 2500 },
  server: { port: 5173 },
});
