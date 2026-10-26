import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { engine } from "./engine";
import "./styles.css";

engine.boot();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
