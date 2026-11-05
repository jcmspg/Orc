// Picks the engine at startup: the real server by default, the in-app
// simulation with `?sim`. `?server=ws://host:port/ws` points elsewhere.

import { FakeEngine } from "./engine";
import { RemoteEngine } from "./remote";
import type { Engine } from "./types";

const params = new URLSearchParams(window.location.search);

export const engine: Engine = params.has("sim")
  ? new FakeEngine()
  : new RemoteEngine(params.get("server") ?? "ws://127.0.0.1:47820/ws");

if (import.meta.hot && engine instanceof RemoteEngine) {
  const remote = engine;
  import.meta.hot.dispose(() => remote.stop());
}
