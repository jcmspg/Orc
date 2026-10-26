import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { engine } from "./engines";
import { board, select } from "./store";
import "./styles.css";

engine.start();
// Dev-only handle for debugging and scripted tests (devtools: `__orc.board.getState()`).
if (import.meta.env.DEV) Object.assign(window, { __orc: { engine, board, select } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
